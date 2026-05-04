import re
import asyncio
import traceback
from tools.browser import BrowserWrapper
from tools.auth import generate_test_credentials, find_and_click_logout, find_login_link


class Executor:
    def __init__(self, browser_wrapper):
        self.browser = browser_wrapper
        self.verified_credentials = None

    async def execute(self, test_case: dict, url: str) -> dict:
        if test_case.get("type") == "auth_flow":
            return await self.execute_auth_flow(test_case, url)

        print(f"[ACT] Executing: {test_case.get('id')} - {test_case.get('description')}")
        result = {
            "test_case": test_case,
            "status": "passed",
            "error": None,
            "screenshot": None,
            "execution_time_ms": 0,
        }

        steps = list(test_case.get("steps", []))
        expected = (test_case.get("expected") or "").lower()
        is_valid_login = (
            not re.search(r'\b(fail|error|invalid|incorrect|wrong|reject|empty)\b', expected, re.IGNORECASE)
            and bool(re.search(r'\b(success|log\s+in|dashboard|valid|welcome)\b', expected, re.IGNORECASE))
        )
        if is_valid_login and self.verified_credentials:
            creds = self.verified_credentials
            for step in steps:
                field = (step.get("field") or "").lower()
                if "email" in field or "user" in field:
                    step["value"] = creds["email"]
                elif "password" in field or "pass" in field:
                    step["value"] = creds["password"]
            print(f"[ACT] Injecting verified credentials: {creds['email']}")

        try:
            await self.browser.open_page(url)
            for step in steps:
                await self.browser.fill_field(step.get("field"), step.get("value"))
            await self.browser.click_submit()
            await self.browser.wait_for_load()

            print(f"[VERIFY] Verifying: {test_case.get('id')}...")
            current_url = await self.browser.get_page_url()
            has_error = await self.browser.has_error_message()

            is_invalid = bool(re.search(r'\b(fail|error|invalid|incorrect|wrong|reject)\b', expected, re.IGNORECASE))
            is_valid   = not is_invalid and bool(re.search(r'\b(success|log\s+in|dashboard|valid)\b', expected, re.IGNORECASE))

            passed = False
            if is_valid:
                for _ in range(6):
                    current_url = await self.browser.get_page_url()
                    has_error   = await self.browser.has_error_message()
                    if not has_error and url not in current_url:
                        passed = True
                        break
                    await asyncio.sleep(0.5)
            elif is_invalid:
                passed = has_error or url in current_url
            else:
                passed = True

            if not passed:
                raise Exception("Verification failed. Expected state not reached.")

        except Exception as e:
            print(f"[ACT/VERIFY] Test {test_case.get('id')} failed: {e}")
            result["status"] = "failed"
            result["error"]  = str(e)
            try:
                result["screenshot"] = await self.browser.take_screenshot(f"error_{test_case.get('id')}")
            except Exception:
                pass
        else:
            try:
                result["screenshot"] = await self.browser.take_screenshot(f"success_{test_case.get('id')}")
            except Exception:
                pass

        return result

    # ── Auth flow ─────────────────────────────────────────────────────────────

    async def execute_auth_flow(self, test_case: dict, url: str) -> dict:
        print("[AUTH] Starting Authentication Flow Test")
        auth_info    = test_case.get("auth_info", {})
        run_context  = {}
        steps        = []

        result = {
            "test_case":         test_case,
            "status":            "passed",
            "credentials_used":  None,
            "error":             None,
            "screenshot":        None,
            "steps":             steps,
            "execution_time_ms": 0,
        }

        credentials = generate_test_credentials()
        if not auth_info.get("has_username_field"):
            credentials["username"] = None
        run_context["credentials"] = credentials
        run_context["login_url"]   = auth_info.get("login_url") or url
        run_context["login_verified"] = False
        result["credentials_used"] = credentials
        print(f"[AUTH] Generated test account: {credentials['email']}")

        if auth_info.get("has_register"):
            reg_step = await self._step_register(auth_info, run_context)
            steps.append(reg_step)

            if reg_step["status"] == "failed" and "already" in (reg_step.get("note") or "").lower():
                print("[AUTH] Duplicate email detected, retrying with new credentials...")
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
            steps.append({"step": "login",        "status": "skipped", "note": f"Skipped — {note}", "screenshot": None})
            steps.append({"step": "verify_login", "status": "skipped", "note": f"Skipped — {note}", "screenshot": None})
        else:
            steps.append({"step": "login",        "status": "skipped", "note": "No register form.", "screenshot": None})
            steps.append({"step": "verify_login", "status": "skipped", "note": "No register form.", "screenshot": None})

        steps.append(await self._step_invalid_login(run_context))
        steps.append(await self._step_logout(run_context))

        failed_steps   = [s for s in steps if s["status"] == "failed"]
        critical_fail  = [s for s in failed_steps if s["step"] in ("register", "login", "verify_login")]

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
            print(f"[AUTH] Credentials stored for reuse: {self.verified_credentials['email']}")

        print(f"[AUTH] Flow complete — status: {result['status']}")
        return result

    # ── Auth step helpers ─────────────────────────────────────────────────────

    async def _step_register(self, auth_info: dict, run_context: dict) -> dict:
        step = {"step": "register", "status": "passed", "note": None, "screenshot": None}
        credentials   = run_context["credentials"]
        register_url  = auth_info.get("register_url")
        try:
            print(f"[AUTH][REGISTER] Navigating to {register_url}")
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
            print("[AUTH][LOGIN] Filling login form with generated credentials")
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
            has_content = any(kw in page_text for kw in ["dashboard", "logout", "log out", "sign out", "profile", "welcome", "my account"])
            email_hint  = credentials["email"].split("@")[0] in page_text
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
            print("[AUTH][INVALID_LOGIN] Testing login with wrong password")
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
            print("[AUTH][LOGOUT] Searching for logout button")
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

async def run_on_browser(browser_name: str, test_cases: list, url: str,
                          emit_fn=None) -> list:
    """Run all test cases on a single browser instance. Returns list of raw results."""
    bw = BrowserWrapper()
    ex = Executor(bw)
    try:
        await bw.start(browser_name)
        results = []
        for tc in test_cases:
            result = await ex.execute(tc, url)
            result["id"]      = tc.get("id", "")
            result["title"]   = tc.get("description", "")
            result["browser"] = browser_name
            # Normalise status to uppercase
            result["status"] = (result.get("status") or "unknown").upper()
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
        print(f"[EXECUTOR] Browser '{browser_name}' crashed: {e}")
        return []
    finally:
        await bw.close()


async def run_all_browsers_parallel(test_cases: list, url: str, browsers: list,
                                     emit_fn=None) -> dict:
    """Run all test cases on every browser in parallel. Returns result matrix dict."""
    tasks = [run_on_browser(b, test_cases, url, emit_fn) for b in browsers]
    all_results = await asyncio.gather(*tasks)

    matrix: dict = {}
    for browser_results in all_results:
        for r in browser_results:
            tid = r.get("id") or "UNKNOWN"
            if tid not in matrix:
                tc = r.get("test_case") or {}
                matrix[tid] = {
                    "title":       r.get("title") or tc.get("description", ""),
                    "description": tc.get("description", ""),
                    "expected":    tc.get("expected", ""),
                    "steps":       tc.get("steps", []),
                    "type":        tc.get("type", "standard"),
                    "browsers":    {},
                }
            matrix[tid]["browsers"][r["browser"]] = {
                "status":     r.get("status", "UNKNOWN"),
                "error":      r.get("error") or "",
                "screenshot": r.get("screenshot") or "",
            }

    for tid, data in matrix.items():
        statuses = [v["status"] for v in data["browsers"].values()]
        if all(s == "PASSED" for s in statuses):
            data["overall"] = "PASSED"
        elif all(s == "FAILED" for s in statuses):
            data["overall"] = "FAILED"
        elif all(s in ("SKIPPED", "PLANNED") for s in statuses):
            data["overall"] = "SKIPPED"
        else:
            data["overall"] = "PARTIAL"

    return matrix
