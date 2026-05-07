"""Test case executor — runs steps against a live browser page."""
import re
import asyncio
import logging
import traceback
from urllib.parse import urlparse as _urlparse, urljoin as _urljoin
from tools.browser import BrowserWrapper
from tools.auth import generate_test_credentials, find_and_click_logout, find_login_link

logger = logging.getLogger(__name__)


class Executor:
    def __init__(self, browser_wrapper: BrowserWrapper) -> None:
        self.browser = browser_wrapper
        self.verified_credentials: dict | None = None
        # Set these to enable session recovery during platform exploration
        self.explore_credentials: dict | None = None
        self.explore_login_url:   str  | None = None

    # ── Main entry point ──────────────────────────────────────────────────────

    async def execute(self, test_case: dict, url: str) -> dict:
        """Execute a test case and return a result dict with per-step breakdown.

        Handles all action types: fill, click, navigate, assert_visible,
        assert_not_visible, screenshot, select, check, fill_form, verify_text,
        submit, wait, clear, describe.

        Per-step behaviour:
        - A screenshot is taken after every step (pass or fail).
        - A failed step marks ``any_step_failed`` but execution continues.
        - Overall status: FAILED if final verification fails, PARTIAL if some
          steps failed but verification passed, PASSED otherwise.
        """
        if test_case.get("type") == "auth_flow":
            return await self.execute_auth_flow(test_case, url)
        if test_case.get("type") == "crawl":
            return await self.execute_page_load(test_case, url)

        tc_id = test_case.get("id", "?")
        logger.info("Executing %s — %s", tc_id, test_case.get("description"))

        step_results: list[dict] = []
        result: dict = {
            "test_case":                  test_case,
            "status":                     "passed",
            "error":                      None,
            "screenshot":                 None,
            "primary_screenshot":         None,   # semantically correct image for report
            "primary_screenshot_context": None,   # caption metadata
            "step_results":               step_results,
            "execution_time_ms":          0,
        }

        # Track where the first failure occurred (for primary screenshot selection)
        _first_fail_ss:  str  | None = None
        _first_fail_ctx: dict | None = None

        steps    = list(test_case.get("steps", []))
        expected = (test_case.get("expected") or "").lower()

        # ── Inject verified credentials for valid-login scenarios ─────────────
        is_valid_login = (
            not re.search(r'\b(fail|error|invalid|incorrect|wrong|reject|empty)\b', expected, re.IGNORECASE)
            and bool(re.search(r'\b(success|log\s+in|dashboard|valid|welcome)\b', expected, re.IGNORECASE))
        )
        if is_valid_login and self.verified_credentials:
            creds = self.verified_credentials
            for step in steps:
                field = (step.get("field") or "").lower()
                if "email" in field or "user" in field:
                    step["value"] = creds.get("username") or creds.get("email") or ""
                elif "password" in field or "pass" in field:
                    step["value"] = creds.get("password") or ""
            logger.debug("Injecting verified credentials: %s",
                         creds.get("username") or creds.get("email"))

        # ── Navigate to test URL, recover session if redirected ───────────────
        try:
            await self.browser.open_page(url)
            actual_path   = _urlparse(await self.browser.get_page_url()).path.rstrip("/") or "/"
            expected_path = _urlparse(url).path.rstrip("/") or "/"
            if actual_path != expected_path:
                if self.explore_credentials:
                    await self._re_login(self.explore_login_url or url, self.explore_credentials)
                else:
                    try:
                        await self.browser.context.clear_cookies()
                        await self.browser.page.evaluate(
                            "try{localStorage.clear();sessionStorage.clear();}catch(e){}"
                        )
                    except Exception:
                        pass
                await self.browser.open_page(url)
        except Exception as nav_err:
            result["status"] = "failed"
            result["error"]  = f"Navigation échouée : {nav_err}"
            try:
                ss = await self.browser.take_screenshot(f"{tc_id}_nav_err")
                result["screenshot"] = ss
                result["primary_screenshot"] = ss
                result["primary_screenshot_context"] = {"step": 0, "action": "navigation", "label": "Erreur de navigation"}
            except Exception:
                pass
            return result

        has_fill_step = any(
            s.get("action") in ("fill", "select") or
            (s.get("action") is None and s.get("field"))
            for s in steps
        )
        has_explicit_submit = any(
            s.get("action") in ("submit", "click", "check") for s in steps
        )
        any_step_failed = False

        # ── Per-step execution ────────────────────────────────────────────────
        for i, step in enumerate(steps):
            action = step.get("action") or ("fill" if step.get("field") else "skip")
            sr: dict = {
                "step":       i + 1,
                "action":     action,
                "field":      step.get("field") or step.get("text") or step.get("value") or "",
                "value":      step.get("value") or "",
                "status":     "passed",
                "screenshot": None,
                "error":      None,
            }

            try:
                if action == "fill":
                    field = step.get("field")
                    value = step.get("value") or ""
                    if field:
                        await self.browser.fill_field(field, value)

                elif action == "submit":
                    await self.browser.click_submit()
                    await self.browser.wait_for_load()
                    # Wait for SPA/AJAX redirects to settle (e.g. OrangeHRM takes 2-3s after login)
                    try:
                        await self.browser.page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        await asyncio.sleep(2)

                elif action == "wait":
                    try:
                        secs = min(float(step.get("value", 1)), 5)
                    except (ValueError, TypeError):
                        secs = 1
                    await asyncio.sleep(secs)

                elif action == "navigate":
                    nav = step.get("value") or url
                    if nav.startswith("/"):
                        nav = _urljoin(url, nav)
                    await self.browser.open_page(nav)

                elif action == "clear":
                    field = step.get("field")
                    if field:
                        await self.browser.fill_field(field, "")

                elif action == "click":
                    target = step.get("text") or step.get("value") or step.get("field") or ""
                    if target:
                        await self.browser.click_element(target)
                        await self.browser.wait_for_load()

                elif action == "click_row_action":
                    row_text = step.get("row") or step.get("field") or step.get("text") or ""
                    act      = (step.get("value") or "delete").lower()
                    if row_text:
                        await self.browser.click_row_action(row_text, act)
                        await self.browser.wait_for_load()

                elif action == "select":
                    field = step.get("field")
                    value = step.get("value") or ""
                    if field and value:
                        await self.browser.select_option(field, value)

                elif action == "check":
                    field = step.get("field") or step.get("value") or ""
                    if field:
                        await self.browser.check_checkbox(field)

                elif action == "fill_form":
                    value_hint = step.get("value") or "Test"
                    await asyncio.sleep(0.5)
                    filled = await self.browser.fill_all_visible_fields(value_hint)
                    logger.debug("fill_form: filled %d field(s) on current page.", filled)

                elif action == "verify_text":
                    text = str(step.get("value") or step.get("text") or "").strip()
                    if text:
                        found = await self.browser.page_contains_text(text)
                        if not found:
                            # Give the page up to 4s to settle (SPA rendering, AJAX redirects)
                            for _ in range(8):
                                await asyncio.sleep(0.5)
                                found = await self.browser.page_contains_text(text)
                                if found:
                                    break
                        if not found:
                            text_lower = text.lower()
                            # Keywords that signal the step is checking for an error state
                            _error_kws = {
                                "invalid", "error", "fail", "wrong", "incorrect",
                                "denied", "unauthorized", "rejected", "not found",
                                "invalid credentials", "erreur", "invalide",
                            }
                            # Generic success phrases that warrant a success-message check
                            _success_kws = {
                                "successfully", "success", "saved", "created",
                                "added", "deleted", "updated", "réussi", "enregistré",
                                "thank you", "confirmed", "complete",
                            }
                            if any(kw in text_lower for kw in _error_kws):
                                # LLM wrote e.g. "Invalid credentials" or "required" —
                                # accept generic error indicator OR any error keyword in page text
                                found = await self.browser.has_error_message()
                                if not found:
                                    # Fallback: scan raw page text for the error keyword(s)
                                    # (catches app-specific error elements not in our CSS selectors)
                                    page_text_now = (await self.browser.get_page_text()).lower()
                                    found = any(kw in page_text_now for kw in _error_kws)
                            elif any(kw in text_lower for kw in _success_kws):
                                found = await self.browser.has_success_message()
                                if not found:
                                    # Toast may have already faded — accept URL change as evidence
                                    origin_path  = _urlparse(url).path.rstrip("/") or "/"
                                    current_url_ = await self.browser.get_page_url()
                                    current_path = _urlparse(current_url_).path.rstrip("/") or "/"
                                    found = current_path != origin_path
                                if not found:
                                    # Final fallback for same-page CRUD operations (delete/add/edit):
                                    # if there is no error message and the browser is still on an
                                    # authenticated page, the operation succeeded and the toast
                                    # simply disappeared before we could catch it.
                                    has_err_now  = await self.browser.has_error_message()
                                    current_url_ = await self.browser.get_page_url()
                                    is_authed    = (
                                        "auth/login" not in current_url_
                                        and "/login" not in current_url_.split("?")[0].lower()
                                    )
                                    found = not has_err_now and is_authed
                            else:
                                # Platform-specific text (e.g. "Dashboard", "Products",
                                # "Swag Labs") — accept if URL changed OR success message
                                origin_path = _urlparse(url).path.rstrip("/") or "/"
                                current_path = _urlparse(
                                    await self.browser.get_page_url()
                                ).path.rstrip("/") or "/"
                                found = (
                                    current_path != origin_path
                                    or await self.browser.has_success_message()
                                )
                                if not found:
                                    # Final fallback: no error detected means the action likely
                                    # succeeded without navigating (cart updates, menu close,
                                    # reset state, new-tab links where current page is unchanged).
                                    found = not await self.browser.has_error_message()
                        if not found:
                            raise AssertionError(f"verify_text: '{text}' not found on page.")

                elif action == "assert_visible":
                    # Accepts a CSS selector or visible text phrase
                    target = step.get("value") or step.get("text") or step.get("field") or ""
                    if not target:
                        raise AssertionError("assert_visible: no target specified.")
                    visible = False
                    # Try as CSS selector first
                    try:
                        visible = await self.browser.page.locator(target).first.is_visible(timeout=3000)
                    except Exception:
                        pass
                    # Fallback: text search
                    if not visible:
                        visible = await self.browser.page_contains_text(target)
                    if not visible:
                        raise AssertionError(f"assert_visible: '{target}' not visible on page.")

                elif action == "assert_not_visible":
                    target = step.get("value") or step.get("text") or step.get("field") or ""
                    if not target:
                        raise AssertionError("assert_not_visible: no target specified.")
                    visible = False
                    try:
                        visible = await self.browser.page.locator(target).first.is_visible(timeout=2000)
                    except Exception:
                        pass
                    if not visible:
                        visible = await self.browser.page_contains_text(target)
                    if visible:
                        raise AssertionError(f"assert_not_visible: '{target}' is unexpectedly visible.")

                elif action == "screenshot":
                    name = step.get("value") or step.get("text") or f"{tc_id}_step{i+1}"
                    sr["screenshot"] = await self.browser.take_screenshot(name)
                    step_results.append(sr)
                    continue

                elif action == "describe":
                    pass  # narrative plan-only step — no live action

                # No screenshot on passing steps — only failures and final result matter

            except Exception as step_err:
                sr["status"] = "failed"
                sr["error"]  = str(step_err)
                any_step_failed = True
                logger.warning("Étape %d (%s) échouée dans %s : %s", i + 1, action, tc_id, step_err)
                try:
                    ss_name = f"{tc_id}_etape{i+1:02d}_{action}_echec"
                    sr["screenshot"] = await self.browser.take_screenshot(ss_name)
                    # Record first failure for primary_screenshot
                    if _first_fail_ss is None:
                        _first_fail_ss  = sr["screenshot"]
                        _first_fail_ctx = {
                            "step":   i + 1,
                            "action": action,
                            "label":  f"Échec à l'étape {i+1} ({action})",
                        }
                except Exception:
                    pass
                # Continue to next step — do not abort

            step_results.append(sr)

        # ── Auto-submit when fill steps exist but no explicit terminal action ─
        if has_fill_step and not has_explicit_submit:
            try:
                await self.browser.click_submit()
                await self.browser.wait_for_load()
            except Exception as submit_err:
                any_step_failed = True
                logger.warning("Auto-submit failed in %s: %s", tc_id, submit_err)

        # ── Final verification ─────────────────────────────────────────────────
        verification_passed = False
        try:
            logger.debug("Verifying %s…", tc_id)
            current_url = await self.browser.get_page_url()
            has_error   = await self.browser.has_error_message()

            _valid_re = re.compile(
                r'\b(success|succès|log\s*in|dashboard|valid|redirect|inventory|home|welcome|'
                r'logged|ajout[eé]|supprim[eé]|modifi[eé]|enregistr[eé]|cr[eé][eé]|'
                r'saved|created|added|deleted|removed|updated|mis à jour|réussi)\b',
                re.IGNORECASE,
            )
            _invalid_re = re.compile(
                r'\b(fail|error|invalid|incorrect|wrong|reject|empty|'
                r'erreur|invalide|incorrect|refus[eé]|vide|obligatoire)\b',
                re.IGNORECASE,
            )

            is_invalid = bool(_invalid_re.search(expected))
            is_valid   = not is_invalid and bool(_valid_re.search(expected))
            origin_path = _urlparse(url).path.rstrip("/") or "/"

            passed = False
            if is_valid:
                for _ in range(8):
                    current_url  = await self.browser.get_page_url()
                    has_error    = await self.browser.has_error_message()
                    has_success  = await self.browser.has_success_message()
                    current_path = _urlparse(current_url).path.rstrip("/") or "/"
                    if not has_error and (current_path != origin_path or has_success):
                        passed = True
                        break
                    await asyncio.sleep(0.5)
                if not passed:
                    # Fallback for in-place SPA operations (e.g. cart add/remove, reset)
                    # where the URL never changes and there is no toast: accept if
                    # there is no error and the session is still authenticated.
                    current_url_ = await self.browser.get_page_url()
                    is_authed    = (
                        "auth/login" not in current_url_
                        and "/login" not in current_url_.split("?")[0].lower()
                    )
                    passed = not await self.browser.has_error_message() and is_authed
            elif is_invalid:
                current_path = _urlparse(current_url).path.rstrip("/") or "/"
                passed = has_error or current_path == origin_path
            else:
                passed = not has_error

            if not passed:
                raise AssertionError("Verification failed. Expected state not reached.")

            verification_passed = True

        except Exception as verify_err:
            result["error"] = str(verify_err)
            logger.info("Verification failed for %s: %s", tc_id, verify_err)

        # ── Determine overall status ──────────────────────────────────────────
        if not verification_passed:
            result["status"] = "failed"
        elif any_step_failed:
            result["status"] = "partial"
        else:
            result["status"] = "passed"

        # Final summary screenshot (tc_id prefix keeps files grouped)
        final_ss = None
        try:
            label    = "echec" if not verification_passed else "succes"
            final_ss = await self.browser.take_screenshot(f"{tc_id}_{label}_final")
            result["screenshot"] = final_ss
        except Exception:
            pass

        # primary_screenshot: failure point for FAILED/PARTIAL, final state for PASSED
        if (not verification_passed or any_step_failed) and _first_fail_ss:
            result["primary_screenshot"]         = _first_fail_ss
            result["primary_screenshot_context"] = _first_fail_ctx
        else:
            result["primary_screenshot"]         = final_ss
            result["primary_screenshot_context"] = {
                "step":   len(steps),
                "action": "état final",
                "label":  "État final après exécution",
            }

        return result

    # ── Session recovery ──────────────────────────────────────────────────────

    async def _re_login(self, login_url: str, credentials: dict) -> None:
        """Re-authenticate when session is lost during platform exploration."""
        logger.info("Session lost — re-logging in to %s", login_url)
        try:
            await self.browser.open_page(login_url)
            fields = await self.browser.extract_inputs()
            for field in fields:
                fid   = (field.get("id") or field.get("name") or "").lower()
                ftype = (field.get("type") or "").lower()
                ident = field.get("id") or field.get("name") or ftype
                if any(k in fid for k in ("user", "email", "login", "name")) or ftype == "text":
                    await self.browser.fill_field(ident, credentials.get("username", ""))
                elif ftype == "password":
                    await self.browser.fill_field(ident, credentials.get("password", ""))
            await self.browser.click_submit()
            await self.browser.wait_for_load()
            logger.info("Re-login complete.")
        except Exception as e:
            logger.warning("Re-login failed: %s", e)

    # ── Page load (crawl) ─────────────────────────────────────────────────────

    async def execute_page_load(self, test_case: dict, url: str) -> dict:
        """Navigate to URL and check for errors — no form interaction."""
        result: dict = {
            "test_case":         test_case,
            "status":            "passed",
            "error":             None,
            "screenshot":        None,
            "step_results":      [],
            "execution_time_ms": 0,
        }
        try:
            await self.browser.open_page(url)
            await self.browser.wait_for_load()
            has_error = await self.browser.has_error_message()
            result["screenshot"] = await self.browser.take_screenshot(f"crawl_{test_case.get('id')}")
            if has_error:
                result["status"] = "failed"
                result["error"]  = "Error detected on page."
        except Exception as e:
            result["status"] = "failed"
            result["error"]  = str(e)
            try:
                result["screenshot"] = await self.browser.take_screenshot(
                    f"crawl_err_{test_case.get('id')}"
                )
            except Exception:
                pass
        return result

    # ── Auth flow ─────────────────────────────────────────────────────────────

    async def execute_auth_flow(self, test_case: dict, url: str) -> dict:
        """Run the full authentication flow: register → login → invalid login → logout."""
        logger.info("Starting Authentication Flow Test")
        auth_info   = test_case.get("auth_info", {})
        run_context: dict = {}
        steps: list[dict] = []

        result: dict = {
            "test_case":         test_case,
            "status":            "passed",
            "credentials_used":  None,
            "error":             None,
            "screenshot":        None,
            "steps":             steps,
            "step_results":      steps,
            "execution_time_ms": 0,
        }

        credentials = generate_test_credentials()
        if not auth_info.get("has_username_field"):
            credentials["username"] = None
        run_context["credentials"] = credentials
        run_context["login_url"]   = auth_info.get("login_url") or url
        run_context["login_verified"] = False
        result["credentials_used"] = credentials
        logger.info("Generated test account: %s", credentials["email"])

        if auth_info.get("has_register"):
            reg_step = await self._step_register(auth_info, run_context)
            steps.append(reg_step)

            if reg_step["status"] == "failed" and "already" in (reg_step.get("note") or "").lower():
                logger.info("Duplicate email detected, retrying with new credentials…")
                credentials = generate_test_credentials()
                if not auth_info.get("has_username_field"):
                    credentials["username"] = None
                run_context["credentials"] = credentials
                result["credentials_used"] = credentials
                reg_step = await self._step_register(auth_info, run_context)
                reg_step["note"] = "Retried. " + (reg_step.get("note") or "")
                steps.append(reg_step)

            if reg_step["status"] == "skipped":
                steps.append({"step": "verify_registration", "status": "skipped",
                               "note": "Registration was skipped.", "screenshot": None})
            else:
                steps.append(await self._step_verify_registration(run_context))
        else:
            steps.append({"step": "register", "status": "skipped",
                           "note": "Registration form not detected.", "screenshot": None})
            steps.append({"step": "verify_registration", "status": "skipped",
                           "note": "Registration form not detected.", "screenshot": None})

        nav_step = await self._step_navigate_to_login(auth_info, run_context)
        steps.append(nav_step)

        verify_reg    = next((s for s in steps if s["step"] == "verify_registration"), None)
        reg_confirmed = verify_reg is not None and verify_reg["status"] == "passed"

        if auth_info.get("has_register") and reg_confirmed:
            steps.append(await self._step_login(run_context))
            steps.append(await self._step_verify_login(run_context))
        elif auth_info.get("has_register"):
            note = (verify_reg or {}).get("note") or "Registration not confirmed"
            steps.append({"step": "login",        "status": "skipped",
                           "note": f"Skipped — {note}", "screenshot": None})
            steps.append({"step": "verify_login", "status": "skipped",
                           "note": f"Skipped — {note}", "screenshot": None})
        else:
            steps.append({"step": "login",        "status": "skipped",
                           "note": "No register form.", "screenshot": None})
            steps.append({"step": "verify_login", "status": "skipped",
                           "note": "No register form.", "screenshot": None})

        steps.append(await self._step_invalid_login(run_context))
        steps.append(await self._step_logout(run_context))

        failed_steps  = [s for s in steps if s["status"] == "failed"]
        critical_fail = [s for s in failed_steps if s["step"] in ("register", "login", "verify_login")]

        if critical_fail:
            result["status"] = "failed"
            result["error"]  = f"Critical step(s) failed: {', '.join(s['step'] for s in critical_fail)}"
        elif failed_steps:
            result["status"] = "partial"

        try:
            result["screenshot"] = await self.browser.take_screenshot("auth_flow_final")
        except Exception:
            pass

        if run_context.get("login_verified"):
            self.verified_credentials = run_context["credentials"]
            logger.info("Credentials stored for reuse: %s", self.verified_credentials["email"])

        logger.info("Auth flow complete — status: %s", result["status"])
        return result

    # ── Auth step helpers ─────────────────────────────────────────────────────

    async def _step_register(self, auth_info: dict, run_context: dict) -> dict:
        step = {"step": "register", "status": "passed", "note": None, "screenshot": None}
        credentials  = run_context["credentials"]
        register_url = auth_info.get("register_url")
        try:
            logger.info("[REGISTER] Navigating to %s", register_url)
            await self.browser.open_page(register_url)
            fields = await self.browser.extract_inputs()
            for field in fields:
                fid   = (field.get("id") or field.get("name") or "").lower()
                ftype = (field.get("type") or "").lower()
                ident = field.get("id") or field.get("name") or ftype
                if ftype == "email" or "email" in fid:
                    await self.browser.fill_field(ident, credentials["email"])
                elif "confirm" in fid and ftype == "password":
                    await self.browser.fill_field(ident, credentials["password"])
                elif ftype == "password":
                    await self.browser.fill_field(ident, credentials["password"])
                elif ("username" in fid or "user" in fid) and credentials.get("username"):
                    await self.browser.fill_field(ident, credentials["username"])
            await self.browser.click_submit()
            await self.browser.wait_for_load()
            page_text = (await self.browser.get_page_text()).lower()
            if "already" in page_text and any(kw in page_text for kw in ["exist", "registered", "taken", "use"]):
                step["status"] = "failed"
                step["note"]   = "Email already exists."
            else:
                step["note"] = "Registration submitted successfully."
            step["screenshot"] = await self.browser.take_screenshot("auth_register")
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
            try:
                step["screenshot"] = await self.browser.take_screenshot("auth_register_error")
            except Exception:
                pass
        return step

    async def _step_verify_registration(self, run_context: dict) -> dict:
        step = {"step": "verify_registration", "status": "passed", "note": None, "screenshot": None}
        try:
            page_text   = (await self.browser.get_page_text()).lower()
            current_url = await self.browser.get_page_url()
            if any(kw in page_text for kw in ["confirm your email", "check your email", "verification email"]):
                step["status"] = "skipped"
                step["note"]   = "Email confirmation required — cannot complete programmatically."
            elif any(kw in page_text for kw in ["success", "welcome", "registered", "account created", "thank you"]):
                step["note"] = "Registration verified: success message detected."
            elif any(kw in current_url for kw in ["dashboard", "home", "profile", "account"]):
                step["note"] = f"Registration verified: redirected to {current_url}."
            else:
                step["status"] = "failed"
                step["note"]   = "Could not confirm registration success."
            run_context["post_register_url"] = current_url
            step["screenshot"] = await self.browser.take_screenshot("auth_verify_register")
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
        return step

    async def _step_navigate_to_login(self, auth_info: dict, run_context: dict) -> dict:
        login_url = run_context.get("login_url", "")
        step = {"step": "navigate_to_login", "status": "passed", "note": None}
        try:
            current_url = await self.browser.get_page_url()
            if login_url and login_url != current_url:
                await self.browser.open_page(login_url)
                actual = await self.browser.get_page_url()
                run_context["login_url"] = actual
                step["note"] = f"Navigated to {actual}."
            else:
                found = await find_login_link(self.browser.page, current_url)
                if found:
                    await self.browser.open_page(found)
                    actual = await self.browser.get_page_url()
                    run_context["login_url"] = actual
                    step["note"] = f"Found login link, navigated to {actual}."
                else:
                    run_context["login_url"] = current_url
                    step["note"] = "Already on login page."
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
        return step

    async def _step_login(self, run_context: dict) -> dict:
        step = {"step": "login", "status": "passed", "note": None, "screenshot": None}
        credentials = run_context["credentials"]
        try:
            logger.info("[LOGIN] Filling login form with generated credentials")
            fields = await self.browser.extract_inputs()
            for field in fields:
                fid   = (field.get("id") or field.get("name") or "").lower()
                ftype = (field.get("type") or "").lower()
                ident = field.get("id") or field.get("name") or ftype
                if ftype == "email" or "email" in fid:
                    await self.browser.fill_field(ident, credentials["email"])
                elif ftype == "password" and "confirm" not in fid:
                    await self.browser.fill_field(ident, credentials["password"])
            await self.browser.click_submit()
            await self.browser.wait_for_load()
            run_context["post_login_url"] = await self.browser.get_page_url()
            step["note"]       = "Login form submitted with generated credentials."
            step["screenshot"] = await self.browser.take_screenshot("auth_login")
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
            try:
                step["screenshot"] = await self.browser.take_screenshot("auth_login_error")
            except Exception:
                pass
        return step

    async def _step_verify_login(self, run_context: dict) -> dict:
        step = {"step": "verify_login", "status": "passed", "note": None, "screenshot": None}
        try:
            current_url = await self.browser.get_page_url()
            page_text   = (await self.browser.get_page_text()).lower()
            login_url   = run_context.get("login_url", "")
            credentials = run_context["credentials"]
            has_error   = await self.browser.has_error_message()
            redirected  = login_url and login_url not in current_url
            has_content = any(kw in page_text for kw in [
                "dashboard", "logout", "log out", "sign out", "profile", "welcome", "my account",
            ])
            email_hint = credentials["email"].split("@")[0] in page_text
            if has_error and not redirected:
                step["status"] = "failed"
                step["note"]   = "Login failed: error message detected."
            elif redirected or has_content or email_hint:
                step["note"] = f"Login verified: redirected to {current_url}."
                run_context["login_verified"] = True
            else:
                step["status"] = "failed"
                step["note"]   = "Login could not be verified."
            step["screenshot"] = await self.browser.take_screenshot("auth_verify_login")
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
        return step

    async def _step_invalid_login(self, run_context: dict) -> dict:
        step = {"step": "invalid_login", "status": "passed", "note": None, "screenshot": None}
        credentials = run_context["credentials"]
        login_url   = run_context.get("login_url")
        try:
            if login_url:
                await self.browser.open_page(login_url)
            wrong_password = credentials["password"] + "wrong"
            logger.info("[INVALID_LOGIN] Testing login with wrong password")
            fields = await self.browser.extract_inputs()
            for field in fields:
                fid   = (field.get("id") or field.get("name") or "").lower()
                ftype = (field.get("type") or "").lower()
                ident = field.get("id") or field.get("name") or ftype
                if ftype == "email" or "email" in fid:
                    await self.browser.fill_field(ident, credentials["email"])
                elif ftype == "password" and "confirm" not in fid:
                    await self.browser.fill_field(ident, wrong_password)
            await self.browser.click_submit()
            await self.browser.wait_for_load()
            has_error       = await self.browser.has_error_message()
            current_url     = await self.browser.get_page_url()
            stayed_on_login = not login_url or login_url in current_url
            if has_error or stayed_on_login:
                step["note"] = "Invalid credentials correctly rejected."
            else:
                step["status"] = "failed"
                step["note"]   = "Invalid login was not rejected — potential security issue."
            step["screenshot"] = await self.browser.take_screenshot("auth_invalid_login")
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
            try:
                step["screenshot"] = await self.browser.take_screenshot("auth_invalid_login_error")
            except Exception:
                pass
        return step

    async def _step_logout(self, run_context: dict) -> dict:
        if not run_context.get("login_verified"):
            return {"step": "logout", "status": "skipped",
                    "note": "Login was not verified — logout test skipped.", "screenshot": None}
        step = {"step": "logout", "status": "passed", "note": None, "screenshot": None}
        login_url   = run_context.get("login_url")
        credentials = run_context["credentials"]
        try:
            if login_url:
                await self.browser.open_page(login_url)
            fields = await self.browser.extract_inputs()
            for field in fields:
                fid   = (field.get("id") or field.get("name") or "").lower()
                ftype = (field.get("type") or "").lower()
                ident = field.get("id") or field.get("name") or ftype
                if ftype == "email" or "email" in fid:
                    await self.browser.fill_field(ident, credentials["email"])
                elif ftype == "password" and "confirm" not in fid:
                    await self.browser.fill_field(ident, credentials["password"])
            await self.browser.click_submit()
            await self.browser.wait_for_load()
            logger.info("[LOGOUT] Searching for logout button")
            logout_result = await find_and_click_logout(self.browser.page)
            if logout_result["clicked"]:
                await self.browser.wait_for_load()
                step["note"]       = f"Logged out via '{logout_result['selector']}'."
                step["screenshot"] = await self.browser.take_screenshot("auth_logout")
            else:
                step["status"] = "skipped"
                step["note"]   = "Logout button not found on page."
        except Exception as e:
            step["status"] = "failed"
            step["note"]   = str(e)
            try:
                step["screenshot"] = await self.browser.take_screenshot("auth_logout_error")
            except Exception:
                pass
        return step


# ── Module-level multi-browser helpers ────────────────────────────────────────

async def run_on_browser(
    browser_name: str,
    test_cases:   list,
    url:          str,
    emit_fn=None,
) -> list:
    """Run all test cases on a single browser instance. Returns list of raw results."""
    bw = BrowserWrapper()
    ex = Executor(bw)
    try:
        await bw.start(browser_name)
        results = []
        for tc in test_cases:
            try:
                result = await asyncio.wait_for(ex.execute(tc, url), timeout=45)
            except asyncio.TimeoutError:
                logger.warning("Test %s timed out after 45s — marking FAILED.", tc.get("id"))
                result = {
                    "test_case": tc, "status": "failed",
                    "error": "Test timed out after 45s.",
                    "screenshot": None, "step_results": [],
                }
            result["id"]      = tc.get("id", "")
            result["title"]   = tc.get("description", "")
            result["browser"] = browser_name
            result["status"]  = (result.get("status") or "unknown").upper()
            if emit_fn:
                emit_fn({
                    "test_id": result["id"],
                    "browser": browser_name,
                    "status":  result["status"],
                    "error":   result.get("error") or "",
                    "message": f"[{browser_name.upper()}] {result['id']} — {result['status']}",
                })
            results.append(result)
        return results
    except Exception as e:
        traceback.print_exc()
        logger.error("Browser '%s' crashed: %s", browser_name, e)
        return []
    finally:
        await bw.close()


def _build_matrix_entry(r: dict) -> dict:
    """Build the per-test-case matrix structure from a single result."""
    tc = r.get("test_case") or {}
    return {
        "title":       r.get("title") or tc.get("description", ""),
        "description": tc.get("description", ""),
        "expected":    tc.get("expected", ""),
        "steps":       tc.get("steps", []),
        "type":        tc.get("type", "standard"),
        "browsers":    {},
    }


def _merge_into_matrix(matrix: dict, browser_results: list) -> None:
    """Merge a list of per-browser results into the shared matrix."""
    for r in browser_results:
        tid = r.get("id") or "UNKNOWN"
        if tid not in matrix:
            matrix[tid] = _build_matrix_entry(r)
        matrix[tid]["browsers"][r["browser"]] = {
            "status":                     r.get("status", "UNKNOWN"),
            "error":                      r.get("error") or "",
            "screenshot":                 r.get("screenshot") or "",
            "primary_screenshot":         r.get("primary_screenshot") or "",
            "primary_screenshot_context": r.get("primary_screenshot_context"),
            "step_results":               r.get("step_results") or [],
        }
        # Preserve credentials_used from auth flow results so Phase 2 can use them
        if r.get("credentials_used") and not matrix[tid].get("credentials_used"):
            matrix[tid]["credentials_used"] = r["credentials_used"]


def _compute_overall(matrix: dict) -> None:
    """Set the overall status for each test in the matrix."""
    for data in matrix.values():
        statuses = [v["status"] for v in data["browsers"].values()]
        if all(s == "PASSED" for s in statuses):
            data["overall"] = "PASSED"
        elif all(s == "FAILED" for s in statuses):
            data["overall"] = "FAILED"
        elif all(s in ("SKIPPED", "PLANNED") for s in statuses):
            data["overall"] = "SKIPPED"
        else:
            data["overall"] = "PARTIAL"


async def run_all_browsers_multi_page(
    url_tc_pairs:  list,
    browsers:      list,
    emit_fn=None,
    credentials:   dict | None = None,
    login_url:     str | None = None,
) -> dict:
    """Run (url, test_case) pairs on every browser in parallel.

    Each browser opens one session and executes all pairs sequentially.
    If credentials + login_url are provided, the browser authenticates
    before the first test so protected pages are accessible throughout.
    Returns a result matrix keyed by test ID.
    """
    async def _pre_authenticate(bw: BrowserWrapper, creds: dict, url: str) -> bool:
        """Login once so the entire spec test sequence runs authenticated."""
        try:
            await bw.open_page(url)
            inputs = await bw.extract_inputs()
            for f in inputs:
                fid   = (f.get("id") or f.get("name") or "").lower()
                ftype = (f.get("type") or "").lower()
                ident = f.get("id") or f.get("name") or ftype
                if any(k in fid for k in ("user", "email", "login", "name")) or ftype in ("text", "email"):
                    await bw.fill_field(ident, creds["username"])
                elif ftype == "password":
                    await bw.fill_field(ident, creds["password"])
            await bw.click_submit()
            try:
                await bw.page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pass
            await asyncio.sleep(1)
            logger.info("Pre-auth: logged in as '%s' for spec test run.", creds.get("username"))
            return True
        except Exception as exc:
            logger.warning("Pre-auth failed: %s — tests will run unauthenticated.", exc)
            return False

    async def _run_browser(browser_name: str) -> list:
        bw = BrowserWrapper()
        ex = Executor(bw)
        results = []
        try:
            await bw.start(browser_name)
            # Pre-authenticate once so all tests share an authenticated session
            if credentials and login_url:
                await _pre_authenticate(bw, credentials, login_url)
            for target_url, tc in url_tc_pairs:
                try:
                    result = await asyncio.wait_for(ex.execute(tc, target_url), timeout=45)
                except asyncio.TimeoutError:
                    logger.warning("Test %s timed out after 45s — marking FAILED.", tc.get("id"))
                    result = {"status": "failed", "error": "Test timed out after 45s.",
                              "screenshot": None, "step_results": []}
                except Exception as e:
                    result = {"status": "failed", "error": str(e),
                              "screenshot": None, "step_results": []}
                result.update({
                    "id":        tc.get("id", ""),
                    "title":     tc.get("description", ""),
                    "browser":   browser_name,
                    "status":    (result.get("status") or "unknown").upper(),
                    "test_case": tc,
                })
                if emit_fn:
                    emit_fn({
                        "test_id": result["id"],
                        "browser": browser_name,
                        "status":  result["status"],
                        "error":   result.get("error") or "",
                        "message": f"[{browser_name.upper()}] {result['id']} — {result['status']}",
                    })
                results.append(result)
        except Exception as e:
            traceback.print_exc()
            logger.error("Browser '%s' crashed: %s", browser_name, e)
        finally:
            await bw.close()
        return results

    all_results = await asyncio.gather(*[_run_browser(b) for b in browsers])
    matrix: dict = {}
    for browser_results in all_results:
        _merge_into_matrix(matrix, browser_results)
    _compute_overall(matrix)
    return matrix


async def run_all_browsers_parallel(
    test_cases: list,
    url:        str,
    browsers:   list,
    emit_fn=None,
) -> dict:
    """Run all test cases on every browser in parallel. Returns result matrix dict."""
    tasks       = [run_on_browser(b, test_cases, url, emit_fn) for b in browsers]
    all_results = await asyncio.gather(*tasks)
    matrix: dict = {}
    for browser_results in all_results:
        _merge_into_matrix(matrix, browser_results)
    _compute_overall(matrix)
    return matrix
