# Main agent — runs the full QA pipeline: auth, discovery, deep tests, report.
import asyncio
import json
import logging
import os
import re
import traceback
from datetime import datetime
from urllib.parse import urlparse, urljoin

from agent.executor import Executor, run_all_browsers_multi_page, run_all_browsers_parallel
from agent.observer import Observer
from agent.planner import Planner
from tools.browser import BrowserWrapper
from tools.trello import create_failure_card, create_run_list
from tools.auth import detect_auth_forms
from config import (
    TEST_USERNAME, TEST_PASSWORD,
    MAX_SECTIONS, MAX_TESTS_PER_PAGE, MAX_CRAWL_PAGES,
    REPORT_DIR,
)

logger = logging.getLogger(__name__)


class CoreAgent:
    """Runs the full test pipeline against a target URL."""


    def _save_and_build_report(
        self,
        url:        str,
        browsers:   list[str],
        test_cases: list[dict],
        matrix:     dict,
        plan_only:  bool = False,
    ) -> dict:
        """Build the standard response dict, persist to JSON, return it."""
        # Deduplication guard — log a warning if the same test ID appears twice
        seen: set[str] = set()
        dupes: list[str] = []
        for tid in matrix:
            if tid in seen:
                dupes.append(tid)
            seen.add(tid)
        if dupes:
            logger.warning("Duplicate test IDs in matrix: %s", dupes)

        total   = len(matrix)
        passed  = sum(1 for d in matrix.values() if d.get("overall") == "PASSED")
        failed  = sum(1 for d in matrix.values() if d.get("overall") == "FAILED")
        partial = sum(1 for d in matrix.values() if d.get("overall") == "PARTIAL")

        report = {
            "timestamp":     datetime.now().isoformat(),
            "url":           url,
            "browsers":      browsers,
            "multi_browser": len(browsers) > 1,
            "plan_only":     plan_only,
            "total_tests":   total,
            "passed":        passed,
            "failed":        failed,
            "partial":       partial,
            "results":       matrix,
        }

        os.makedirs(REPORT_DIR, exist_ok=True)
        path = os.path.join(REPORT_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)

        logger.info("Report saved → %s", path)
        print(f"[REPORT] Saved to {path}")
        print(f"[REPORT] Total: {total} | Passed: {passed} | Failed: {failed} | Partial: {partial}")

        # Trello cards for failures — cap at 10 (FAILED first, then PARTIAL)
        _MAX_TRELLO = 10
        priority_tids = (
            [tid for tid, d in matrix.items() if d.get("overall") == "FAILED"] +
            [tid for tid, d in matrix.items() if d.get("overall") == "PARTIAL"]
        )
        if priority_tids:
            # Create a dedicated Trello list for this run automatically
            run_list_id: str | None = create_run_list(url)
            if run_list_id:
                logger.info("Trello: run list created (%s)", run_list_id)
            else:
                logger.info("Trello: no run list created — cards go to TRELLO_LIST_ID fallback.")

            for tid in priority_tids[:_MAX_TRELLO]:
                data = matrix[tid]
                failed_browsers = [
                    b for b, bd in data.get("browsers", {}).items()
                    if bd.get("status") not in ("PASSED", "SKIPPED")
                ]
                error_str = " | ".join(
                    f"{b}: {data['browsers'][b].get('error', 'failed')}"
                    for b in failed_browsers
                )
                screenshot = next(
                    (
                        data["browsers"][b].get("screenshot")
                        for b in browsers
                        if b in data.get("browsers", {}) and data["browsers"][b].get("screenshot")
                    ),
                    None,
                )
                create_failure_card(
                    test_id=tid,
                    error_details=error_str or "Test failed",
                    screenshot_path=screenshot,
                    description=data.get("description", ""),
                    list_id=run_list_id,
                )
            skipped = len(priority_tids) - min(len(priority_tids), _MAX_TRELLO)
            if skipped > 0:
                logger.info("Trello: capped at %d cards (%d additional failures skipped).",
                            _MAX_TRELLO, skipped)

        report["report_file"] = os.path.basename(path)
        return report



    @staticmethod
    def _find_credentials_from_steps(test_cases: list[dict]) -> dict | None:
        """Extract valid login credentials from the 'valid login' test case steps."""
        for tc in test_cases:
            expected = (tc.get("expected") or "").lower()
            if not re.search(r"\b(success|redirect|dashboard|inventory|home|welcome|logged)\b", expected):
                continue
            if re.search(r"\b(fail|error|invalid|wrong)\b", expected):
                continue
            creds: dict[str, str] = {}
            for step in tc.get("steps", []):
                if step.get("action") not in (None, "fill"):
                    continue
                field = (step.get("field") or "").lower()
                value = (step.get("value") or "").strip()
                if not value:
                    continue
                if any(k in field for k in ("user", "email", "login", "name")):
                    creds["username"] = value
                elif "pass" in field:
                    creds["password"] = value
            if creds.get("username") and creds.get("password"):
                return creds
        return None

    @staticmethod
    def _extract_credentials_from_page(page_text: str) -> dict | None:
        """Regex fallback: find visible demo credentials in page text."""
        _bad = {"password", "pass", "pwd", "login", "email", "enter",
                "your", "the", "a", "username", "user", "name"}
        username = None
        password = None

        for pat in (
            r"accepted usernames?(?:\s+are)?[:\s]+([a-zA-Z0-9_.\-]+)",
            r"(?:demo|sample|test|example|default)\s+(?:username|user|login)[:\s]+([a-zA-Z0-9_.\-]+)",
            r"(?:username|user|login):\s*([a-zA-Z0-9_.\-]{3,30})",
        ):
            m = re.search(pat, page_text, re.IGNORECASE)
            if m:
                c = m.group(1).strip()
                if c.lower() not in _bad and len(c) >= 3:
                    username = c
                    break

        for pat in (
            r"password\s+for\s+(?:all\s+)?users?[:\s]+([a-zA-Z0-9_@!#$%^&*()\-]{4,})",
            r"(?:password|pass|pwd):\s*([a-zA-Z0-9_@!#$%^&*()\-]{4,})",
        ):
            m = re.search(pat, page_text, re.IGNORECASE)
            if m:
                c = m.group(1).strip()
                if c.lower() not in _bad and len(c) >= 4:
                    password = c
                    break

        if username and password:
            logger.info("Page credentials found: username=%r", username)
            print(f"[EXTRACT] Page credentials found: username='{username}', password='{password}'")
            return {"username": username, "password": password}
        return None

    async def _try_default_credentials(
        self,
        url: str,
        browser_type: str,
    ) -> dict | None:
        """Try a curated list of common default credentials against the login form.

        Used as last resort when a platform has no self-registration and shows
        no demo credentials on the page (e.g. OrangeHRM, WordPress, Jira, etc.).
        Tries each pair and checks whether the URL changes after submit.
        """
        _DEFAULTS = [
            ("Admin",     "admin123"),
            ("admin",     "admin"),
            ("admin",     "admin123"),
            ("admin",     "Admin123"),
            ("admin",     "password"),
            ("admin",     "Password1"),
            ("admin",     "Admin@123"),
            ("admin",     "1234"),
            ("admin",     "12345678"),
            ("user",      "user"),
            ("user",      "password"),
            ("test",      "test"),
            ("demo",      "demo"),
            ("root",      "root"),
            ("admin@admin.com", "admin"),
            ("admin@admin.com", "admin123"),
        ]

        logger.info("Trying common default credentials against %s…", url)
        bw = BrowserWrapper()
        try:
            await bw.start(browser_type)
            orig_path = urlparse(url).path.rstrip("/") or "/"

            for username, password in _DEFAULTS:
                try:
                    await bw.open_page(url)
                    fields = await bw.extract_inputs()
                    filled = 0
                    for field in fields:
                        fid   = (field.get("id") or field.get("name") or "").lower()
                        ftype = (field.get("type") or "").lower()
                        ident = field.get("id") or field.get("name") or ftype
                        if any(k in fid for k in ("user", "email", "login", "name")) or ftype in ("text", "email"):
                            await bw.fill_field(ident, username)
                            filled += 1
                        elif ftype == "password":
                            await bw.fill_field(ident, password)
                            filled += 1
                    if filled < 2:
                        break  # no usable login form found — stop trying
                    await bw.click_submit()
                    try:
                        await bw.page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    post_path = urlparse(await bw.get_page_url()).path.rstrip("/") or "/"
                    if post_path != orig_path:
                        logger.info("Default credentials worked: username=%r", username)
                        print(f"[PHASE 1] Default credentials found: {username}")
                        return {"username": username, "password": password}
                except Exception:
                    continue
        except Exception as e:
            logger.warning("Default credential probe failed: %s", e)
        finally:
            await bw.close()
        return None

    # --- phase 1: auth ---

    async def _phase_auth(
        self,
        url:      str,
        browsers: list[str],
        emit_fn,
    ) -> tuple[dict, dict | None]:
        """Test the auth page; return (auth_matrix, verified_credentials)."""
        print(f"[PHASE 1] Auth — observing {url}")
        if emit_fn:
            emit_fn({"log": "[PHASE 1] Authentification — analyse de la page..."})

        bw       = BrowserWrapper()
        observer = Observer(bw)
        planner  = Planner()

        try:
            await bw.start(browsers[0])
            elements, page_info = await observer.observe(url)
            page_text = page_info.get("page_text", "")
        except Exception as exc:
            logger.error("Phase 1 observe failed: %s", exc)
            await bw.close()
            return {}, None
        finally:
            await bw.close()

        # Check if ENV credentials are set — use them directly
        env_creds: dict | None = None
        if TEST_USERNAME and TEST_PASSWORD:
            env_creds = {"username": TEST_USERNAME, "password": TEST_PASSWORD}
            logger.info("Using TEST_USERNAME / TEST_PASSWORD from environment.")
            print(f"[PHASE 1] Using env credentials: username='{TEST_USERNAME}'")

        # Detect auth forms
        fields    = [e for e in elements if e.get("category") == "form_field"]
        auth_info = detect_auth_forms(fields, url, page_text)
        is_auth   = auth_info["has_login"] or auth_info["has_register"]

        if not is_auth:
            # No login/register page — skip auth testing entirely.
            # The rest of the app will be tested in Phase 3 via nav discovery.
            logger.info("No auth form detected — skipping auth phase.")
            if emit_fn:
                emit_fn({"log": "[PHASE 1] Aucun formulaire d'authentification — phase ignorée."})
            page_creds = self._extract_credentials_from_page(page_text)
            return {}, env_creds or page_creds

        # Auth page confirmed — generate and run auth test cases
        test_cases = planner.plan(elements, url, page_info)

        print(f"[PHASE 1] {len(test_cases)} auth test cases generated.")
        if emit_fn:
            emit_fn({"log": f"[PHASE 1] {len(test_cases)} tests d'authentification générés."})

        # Execute on all browsers
        auth_matrix = await run_all_browsers_parallel(test_cases, url, browsers, emit_fn)

        # Determine verified credentials — priority order:
        # 1. Env vars  2. Credentials the auth flow actually registered/used
        #              3. Visible demo credentials on the page
        #              4. Hardcoded values in the "valid login" test case steps
        credentials = env_creds

        if not credentials:
            # Auth flow (AUTH_FLOW_001) stores credentials_used in the matrix entry.
            # Only use these if the platform HAS a registration form — otherwise the
            # mock email was never registered and will always fail on login.
            if auth_info.get("has_register"):
                auth_entry = auth_matrix.get("AUTH_FLOW_001", {})
                used = auth_entry.get("credentials_used")
                if used:
                    username = used.get("email") or used.get("username") or ""
                    password = used.get("password") or ""
                    if username and password:
                        credentials = {"username": username, "password": password}
                        logger.info("Using credentials from auth flow registration: %s", username)

        if not credentials:
            credentials = self._extract_credentials_from_page(page_text)

        if not credentials:
            credentials = self._find_credentials_from_steps(test_cases)

        if not credentials and auth_info.get("has_login") and not auth_info.get("has_register"):
            # No registration form and no credentials found anywhere —
            # try common default credentials (e.g. OrangeHRM: Admin/admin123)
            if emit_fn:
                emit_fn({"log": "[PHASE 1] Tentative de connexion avec identifiants par défaut…"})
            credentials = await self._try_default_credentials(url, browsers[0])

        if credentials:
            logger.info("Credentials resolved: username=%r", credentials.get("username"))
        else:
            logger.info("No credentials found — Phase 2 will discover public nav only.")

        return auth_matrix, credentials

    # --- phase 2: discovery ---

    async def _phase_discover(
        self,
        url:         str,
        credentials: dict | None,
        browser_type: str,
        emit_fn,
    ) -> list[dict]:
        """Login if credentials are available, then extract all nav/sidebar links.

        Works for both authenticated apps (logs in first) and public apps (navigates
        directly). Returns list of {text, href, norm}.
        """
        if credentials:
            print(f"[PHASE 2] Discovery — logging in as '{credentials['username']}'")
            if emit_fn:
                emit_fn({"log": f"[PHASE 2] Découverte — connexion en tant que '{credentials['username']}'"})
        else:
            logger.info("No credentials — discovering nav links without authentication.")
            if emit_fn:
                emit_fn({"log": "[PHASE 2] Découverte sans authentification..."})

        bw = BrowserWrapper()
        try:
            await bw.start(browser_type)
            await bw.open_page(url)

            if credentials:
                # Fill login form and submit
                fields = await bw.extract_inputs()
                for field in fields:
                    fid   = (field.get("id") or field.get("name") or "").lower()
                    ftype = (field.get("type") or "").lower()
                    ident = field.get("id") or field.get("name") or ftype
                    if any(k in fid for k in ("user", "email", "login", "name")) or ftype in ("text", "email"):
                        await bw.fill_field(ident, credentials["username"])
                    elif ftype == "password":
                        await bw.fill_field(ident, credentials["password"])

                await bw.click_submit()
                try:
                    await bw.page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass
                await asyncio.sleep(2)

                post_url  = await bw.get_page_url()
                post_path = urlparse(post_url).path.rstrip("/") or "/"
                orig_path = urlparse(url).path.rstrip("/") or "/"

                if post_path == orig_path:
                    logger.warning("Login may have failed — URL did not change after submit.")
                    print("[PHASE 2] Login may have failed — URL unchanged.")
                    # Still attempt nav discovery from the current page rather than aborting
                else:
                    print(f"[PHASE 2] Logged in — now at {post_url}")
                    if emit_fn:
                        emit_fn({"log": f"[PHASE 2] Connecté — exploration depuis {post_url}"})

            # Screenshot dashboard
            await bw.take_screenshot("phase2_dashboard")

            # Try to expand hidden navigation (hamburger / sidebar toggles)
            _toggle_sels = [
                "[class*='hamburger']", "[class*='burger']",
                "[class*='menu-toggle']", "[class*='nav-toggle']",
                "[class*='sidebar-toggle']", "[class*='navbar-toggler']",
                "[aria-label*='menu' i]", "[aria-label*='toggle' i]",
                "[aria-label*='navigation' i]", "[aria-controls*='nav' i]",
                "#menu-toggle", "#nav-toggle", ".menu-icon", ".nav-icon",
            ]
            for _tsel in _toggle_sels:
                try:
                    _loc = bw.page.locator(_tsel).first
                    if await _loc.count() > 0 and await _loc.is_visible():
                        await _loc.click()
                        await asyncio.sleep(0.8)
                        logger.info("Phase 2: expanded nav via '%s'", _tsel)
                        break
                except Exception:
                    continue

            # Extract all nav/sidebar/menu links
            base_domain = urlparse(url).netloc.removeprefix("www.")
            raw_links: list[str] = await bw.page.evaluate("""
                () => {
                    const sel = 'nav a, .sidebar a, aside a, [role="navigation"] a, '
                              + '[class*="menu"] a, header a, [class*="nav"] a';
                    return [...new Set([...document.querySelectorAll(sel)].map(a => a.href))]
                        .filter(h => h && !h.startsWith('javascript:') && !h.includes('#')
                               && !h.match(/\\.(pdf|zip|jpg|png|gif|svg|css|js)$/i));
                }
            """)

            def _clean_text(t: str) -> str:
                cleaned = ""
                for ch in t:
                    cp = ord(ch)
                    if 0xE000 <= cp <= 0xF8FF:   # Basic PUA (icon fonts)
                        continue
                    if 0xF0000 <= cp <= 0xFFFFF:  # Supplementary PUA
                        continue
                    if cp < 0x20 or cp == 0x7F:   # Control chars
                        cleaned += ' '
                        continue
                    cleaned += ch
                return ' '.join(cleaned.split())

            _SKIP_KWS = ("logout", "signout", "sign-out", "log-out")
            sections: list[dict] = []
            seen_paths: set[str] = set()
            for link in raw_links:
                parsed = urlparse(link)
                if parsed.netloc.removeprefix("www.") != base_domain:
                    continue
                norm = parsed._replace(fragment="", query="").geturl()
                if norm in seen_paths:
                    continue
                if any(kw in link.lower() for kw in _SKIP_KWS):
                    continue
                seen_paths.add(norm)
                # Get link text
                try:
                    text = await bw.page.eval_on_selector(
                        f"[href='{link}'], a[href='{parsed.path}']",
                        "el => (el.innerText || el.textContent || '').trim()",
                    )
                except Exception:
                    text = parsed.path

                text = _clean_text(text or parsed.path)
                if not text:
                    text = parsed.path
                sections.append({"text": text, "href": link, "norm": norm})

            sections = sections[:MAX_SECTIONS]
            print(f"[PHASE 2] Discovered {len(sections)} sections: {[s['text'] for s in sections]}")
            if emit_fn:
                emit_fn({"log": f"[PHASE 2] {len(sections)} sections découvertes."})
            return sections

        except Exception as exc:
            traceback.print_exc()
            logger.error("Phase 2 discovery failed: %s", exc)
            return []
        finally:
            await bw.close()

    # --- phase 3: deep test ---

    async def _phase_deep_test(
        self,
        url:         str,
        sections:    list[dict],
        credentials: dict | None,
        browsers:    list[str],
        emit_fn,
    ) -> dict:
        """Visit every discovered section, generate and run tests. Returns results matrix."""
        from tools.llm import generate_quick_test_cases

        matrix: dict = {}
        base_domain = urlparse(url).netloc.removeprefix("www.")

        print(f"[PHASE 3] Deep testing — {len(sections)} sections × {len(browsers)} browser(s)")
        if emit_fn:
            emit_fn({"log": f"[PHASE 3] Tests approfondis — {len(sections)} sections..."})

        for i, section in enumerate(sections, 1):
            section_url  = section["href"]
            section_name = section["text"][:30]
            section_id   = f"SEC{i:03d}"

            print(f"[PHASE 3] ({i}/{len(sections)}) {section_name} → {section_url}")
            if emit_fn:
                emit_fn({"log": f"[PHASE 3] Section {i}/{len(sections)}: {section_name}"})


            bw       = BrowserWrapper()
            observer = Observer(bw)
            planner  = Planner()

            page_elements: list[dict] = []
            page_info:     dict       = {}
            section_ok                = True

            try:
                await bw.start(browsers[0])

                # Login first if credentials available
                if credentials:
                    try:
                        await bw.open_page(url)
                        fields = await bw.extract_inputs()
                        for field in fields:
                            fid   = (field.get("id") or field.get("name") or "").lower()
                            ftype = (field.get("type") or "").lower()
                            ident = field.get("id") or field.get("name") or ftype
                            if any(k in fid for k in ("user", "email", "login", "name")) or ftype in ("text", "email"):
                                await bw.fill_field(ident, credentials["username"])
                            elif ftype == "password":
                                await bw.fill_field(ident, credentials["password"])
                        await bw.click_submit()
                        try:
                            await bw.page.wait_for_load_state("networkidle", timeout=6000)
                        except Exception:
                            pass
                        await asyncio.sleep(1)
                    except Exception as login_exc:
                        logger.warning("Phase 3 login failed for %s: %s — observing without auth.",
                                       section_name, login_exc)

                # Navigate to section
                page_elements, page_info = await observer.observe(section_url)

            except Exception as exc:
                logger.error("Phase 3 observe failed for %s: %s", section_url, exc)
                print(f"[PHASE 3] Error observing {section_name}: {exc}")
                section_ok = False
            finally:
                await bw.close()

            # Record page-load test
            load_tc_id = f"{section_id}_LOAD"
            matrix[load_tc_id] = {
                "title":       f"Chargement: {section_name}",
                "description": f"Chargement: {section_name}",
                "expected":    "Page sans erreur",
                "steps":       [],
                "type":        "crawl",
                "browsers":    {browsers[0]: {
                    "status":     "PASSED" if section_ok else "FAILED",
                    "error":      "" if section_ok else "Could not observe page",
                    "screenshot": "",
                }},
                "overall": "PASSED" if section_ok else "FAILED",
            }
            if emit_fn:
                emit_fn({"test_id": load_tc_id, "browser": browsers[0],
                          "status": matrix[load_tc_id]["overall"]})

            if not section_ok:
                continue

            # Generate tests if page has interactive elements
            fields      = [e for e in page_elements if e.get("category") == "form_field"]
            page_ctx    = planner.detect_page_context(page_elements)
            has_actions = any(e.get("category") == "action" for e in page_elements)
            has_table   = any(e.get("category") == "data_display" for e in page_elements)

            if not (fields or has_actions or has_table):
                logger.debug("No interactive elements on %s — skipping test generation.", section_name)
                continue

            print(f"[PHASE 3] Generating tests for '{section_name}' (context={page_ctx})")
            if emit_fn:
                emit_fn({"log": f"[PHASE 3] Génération tests: {section_name} ({page_ctx})"})

            try:
                test_cases = await asyncio.to_thread(
                    planner.plan, page_elements, section_url, page_info
                )
            except Exception as exc:
                logger.error("Test generation failed for %s: %s", section_name, exc)
                print(f"[PHASE 3] Test gen failed for {section_name}: {exc}")
                continue

            # Assign unique IDs prefixed with section ID
            url_tc_pairs: list[tuple[str, dict]] = []
            seen_tc_ids: set[str] = set()
            for j, tc in enumerate(test_cases[:MAX_TESTS_PER_PAGE], 1):
                raw_id  = tc.get("id", f"T{j:03d}")
                safe_id = f"{section_id}_{raw_id}"
                # Guard against duplicate IDs within this section
                if safe_id in seen_tc_ids:
                    safe_id = f"{safe_id}_{j}"
                seen_tc_ids.add(safe_id)
                tc["id"]         = safe_id
                tc["target_url"] = section_url
                url_tc_pairs.append((section_url, tc))

            # Execute on all browsers
            section_matrix = await run_all_browsers_multi_page(
                url_tc_pairs, browsers, emit_fn
            )

            # Merge — check for matrix-level collisions
            for tid, data in section_matrix.items():
                if tid in matrix:
                    logger.warning("Test ID collision across sections: %s — appending suffix", tid)
                    tid = f"{tid}_dup{i}"
                matrix[tid] = data

        print(f"[PHASE 3] Done — {len(matrix)} results collected.")
        return matrix



    async def _crawl_and_test(
        self,
        start_url: str,
        browsers:  list[str],
        emit_fn,
    ) -> dict:
        """BFS crawl for unauthenticated or pre-login pages."""
        from tools.llm import generate_quick_test_cases

        base_domain = urlparse(start_url).netloc.removeprefix("www.")
        visited:    set[str]  = set()
        queue:      list[str] = [start_url]
        discovered: list      = []
        found_credentials: dict | None = None

        print(f"[CRAWL] Starting from {start_url}")
        if emit_fn:
            emit_fn({"log": f"[CRAWL] Démarrage crawl depuis {start_url}"})

        bw = BrowserWrapper()
        try:
            await bw.start(browsers[0])

            while queue and len(discovered) < MAX_CRAWL_PAGES:
                current = queue.pop(0)
                norm    = urlparse(current)._replace(fragment="", query="").geturl()
                if norm in visited:
                    continue
                visited.add(norm)

                path = urlparse(current).path or "/"
                print(f"[CRAWL] ({len(discovered)+1}/{MAX_CRAWL_PAGES}) {path}")
                if emit_fn:
                    emit_fn({"log": f"[CRAWL] {path}"})

                page_entry: dict = {
                    "url": current, "path": path,
                    "elements": [], "page_text": "",
                    "has_error": False, "screenshot": None, "error": None,
                }
                try:
                    await bw.open_page(current)
                    await bw.wait_for_load()

                    raw_links: list[str] = await bw.page.evaluate("""
                        () => [...new Set([...document.querySelectorAll('a[href]')]
                            .map(a => a.href)
                            .filter(h => h && !h.startsWith('javascript:')
                                   && !h.includes('#')
                                   && !h.match(/\\.(pdf|zip|jpg|png|gif|svg|css|js)$/i)))]
                    """)
                    for link in raw_links:
                        lnorm = urlparse(link)._replace(fragment="", query="").geturl()
                        if (
                            urlparse(link).netloc.removeprefix("www.") == base_domain
                            and lnorm not in visited
                            and lnorm not in queue
                        ):
                            queue.append(link)

                    # Use observer to get ALL elements
                    observer    = Observer(bw)
                    elements, _ = await observer.observe(current)
                    page_entry["elements"]   = elements
                    page_entry["page_text"]  = (await bw.get_page_text())
                    page_entry["has_error"]  = await bw.has_error_message()
                    page_entry["screenshot"] = await bw.take_screenshot(
                        f"crawl_p{len(discovered)+1:03d}"
                    )

                    if not found_credentials:
                        c = self._extract_credentials_from_page(page_entry["page_text"])
                        if c:
                            found_credentials = c
                            print(f"[CRAWL] Credentials detected on {path}")

                except Exception as exc:
                    page_entry["has_error"] = True
                    page_entry["error"]     = str(exc)
                    logger.warning("Crawl error on %s: %s", current, exc)

                discovered.append(page_entry)

        except Exception as exc:
            traceback.print_exc()
            logger.error("Crawl engine error: %s", exc)
        finally:
            await bw.close()

        print(f"[CRAWL] Discovered {len(discovered)} pages.")

        # Generate test cases per page
        url_tc_pairs: list[tuple[str, dict]] = []
        planner = Planner()

        for i, page in enumerate(discovered, 1):
            page_id = f"PAGE{i:03d}"

            # Always add a page-load test
            load_tc: dict = {
                "id":          page_id,
                "description": f"Chargement {page['path'][:35]}",
                "expected":    "Page sans erreur",
                "steps":       [],
                "type":        "crawl",
                "target_url":  page["url"],
            }
            url_tc_pairs.append((page["url"], load_tc))

            # Auth type injection
            fields    = [e for e in page["elements"] if e.get("category") == "form_field"]
            ctx       = {}
            auth_info = detect_auth_forms(fields, page["url"], page.get("page_text", ""))
            if auth_info.get("has_login"):
                ctx["auth_type"] = "login"
            elif auth_info.get("has_register"):
                ctx["auth_type"] = "register"

            # Page context from observer data
            page_ctx = planner.detect_page_context(page["elements"])
            if page_ctx != "auth":
                ctx.update({
                    "page_type": page_ctx,
                    "buttons":   [e["text"] for e in page["elements"] if e.get("category") == "action"][:10],
                    "has_table": any(e.get("category") == "data_display" for e in page["elements"]),
                    "action_links": [e["text"] for e in page["elements"] if e.get("category") == "action"][:15],
                })

            has_interactive = (
                fields
                or any(e.get("category") == "data_display" for e in page["elements"])
                or len([e for e in page["elements"] if e.get("category") == "action"]) > 1
            )
            if not has_interactive:
                continue

            print(f"[CRAWL] Generating tests for {page['path']} (context={page_ctx})")
            if emit_fn:
                emit_fn({"log": f"[CRAWL] Génération tests: {page['path']}"})

            try:
                sub_cases = await asyncio.to_thread(
                    generate_quick_test_cases,
                    fields, page["url"],
                    page["page_text"][:300],
                    ctx,
                )
                for j, tc in enumerate(sub_cases, 1):
                    tc["id"]         = f"{page_id}_T{j:02d}"
                    tc["target_url"] = page["url"]
                    url_tc_pairs.append((page["url"], tc))
            except Exception as exc:
                logger.error("Test gen failed for %s: %s", page["path"], exc)

        print(f"[CRAWL] Total test cases: {len(url_tc_pairs)}")
        if emit_fn:
            emit_fn({"log": f"[CRAWL] {len(url_tc_pairs)} tests — exécution..."})

        matrix = await run_all_browsers_multi_page(url_tc_pairs, browsers, emit_fn)
        return matrix



    async def run_multi_browser(
        self,
        url:      str,
        browsers: list[str],
        emit_fn=None,
    ) -> dict:
        """Full 4-phase run: Auth → Discover → Deep-test → Report."""
        if not browsers:
            browsers = ["chromium"]

        try:
            # phase 1: auth
            auth_matrix, credentials = await self._phase_auth(url, browsers, emit_fn)

            # phase 2: discover nav sections
            sections = await self._phase_discover(url, credentials, browsers[0], emit_fn)

            # phase 3: deep test
            if sections:
                deep_matrix = await self._phase_deep_test(
                    url, sections, credentials, browsers, emit_fn
                )
            else:
                # Fallback: unauthenticated BFS crawl
                print("[PHASE 3] No sections found — falling back to unauthenticated crawl.")
                if emit_fn:
                    emit_fn({"log": "[PHASE 3] Crawl non authentifié (aucune section détectée)."})
                deep_matrix = await self._crawl_and_test(url, browsers, emit_fn)

            # Merge matrices — auth results first
            matrix: dict = {}
            matrix.update(auth_matrix)
            for tid, data in deep_matrix.items():
                if tid in matrix:
                    logger.warning("Matrix merge collision on '%s' — suffixing.", tid)
                    tid = f"{tid}_DEEP"
                matrix[tid] = data

        except Exception as exc:
            traceback.print_exc()
            return {"error": repr(exc)}

        return self._save_and_build_report(url, browsers, [], matrix)

    async def run_multi_browser_with_spec(
        self,
        url:       str,
        spec_text: str,
        browsers:  list[str],
        emit_fn=None,
    ) -> dict:
        """Requirements-driven run: execute tests derived from the spec file against the URL.

        Flow:
          1. If no URL → generate test plan (PLANNED) only, no execution.
          2. If URL provided:
             a. Phase 1 auth — resolve credentials from the live app.
             b. Observe the URL (authenticated if possible) — get real field names.
             c. Generate test cases from spec + real fields (LLM 4-pass).
             d. Execute every spec test case against the live app.
             e. Merge auth results + spec execution results and report.
        """
        if not browsers:
            browsers = ["chromium"]

        has_url = bool(url and url.strip())
        planner = Planner()

        # no URL given — just generate a test plan, no execution
        if not has_url:
            print("[SPEC] No URL provided — generating requirements test plan only.")
            if emit_fn:
                emit_fn({"log": "[SPEC] Pas d'URL — génération du plan de test uniquement."})
            spec_cases = await asyncio.to_thread(
                planner.plan_from_spec, spec_text, [], "", {}
            )
            spec_matrix = {
                tc["id"]: {
                    "title":       tc.get("description", ""),
                    "description": tc.get("description", ""),
                    "expected":    tc.get("expected", ""),
                    "steps":       tc.get("steps", []),
                    "type":        tc.get("type", "standard"),
                    "browsers":    {},
                    "overall":     "PLANNED",
                }
                for tc in spec_cases
            }
            return self._save_and_build_report("", browsers, spec_cases, spec_matrix, plan_only=True)

        # full run with spec + URL
        print(f"[SPEC] Requirements-driven test run on {url}")
        if emit_fn:
            emit_fn({"log": f"[SPEC] Tests guidés par exigences sur {url}"})

        try:
            # phase 1: auth
            auth_matrix, credentials = await self._phase_auth(url, browsers, emit_fn)

            # observe the page to get real field names for the LLM
            print("[SPEC] Observing live page to extract real form fields…")
            if emit_fn:
                emit_fn({"log": "[SPEC] Observation de la page pour les champs réels…"})

            bw             = BrowserWrapper()
            observer       = Observer(bw)
            real_fields:    list[dict] = []
            page_info_live: dict       = {}
            spec_sections:  list[dict] = []
            try:
                await bw.start(browsers[0])
                await bw.open_page(url)
                if credentials:
                    # Log in — then observe the POST-LOGIN page (dashboard), not the login URL
                    login_fields = await bw.extract_inputs()
                    for f in login_fields:
                        fid   = (f.get("id") or f.get("name") or "").lower()
                        ftype = (f.get("type") or "").lower()
                        ident = f.get("id") or f.get("name") or ftype
                        if any(k in fid for k in ("user", "email", "login", "name")) or ftype in ("text", "email"):
                            await bw.fill_field(ident, credentials["username"])
                        elif ftype == "password":
                            await bw.fill_field(ident, credentials["password"])
                    await bw.click_submit()
                    try:
                        await bw.page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    await asyncio.sleep(1)
                    # Use observe_current so we see the dashboard, not the login page
                    elements, page_info_live = await observer.observe_current()
                else:
                    elements, page_info_live = await observer.observe(url)
                real_fields = [e for e in elements if e.get("category") == "form_field"]
                logger.info("Observed %d elements (%d form fields) at %s",
                            len(elements), len(real_fields),
                            page_info_live.get("current_url", url))
            except Exception as obs_exc:
                logger.warning("Live observation failed: %s — proceeding without real fields.", obs_exc)
            finally:
                await bw.close()

            # phase 2: grab real section URLs so nav steps are accurate
            print("[SPEC] Discovering app sections for accurate navigation…")
            if emit_fn:
                emit_fn({"log": "[SPEC] Découverte des sections pour navigation précise…"})
            spec_sections = await self._phase_discover(url, credentials, browsers[0], emit_fn)
            if spec_sections:
                print(f"[SPEC] {len(spec_sections)} sections discovered — LLM will use real URLs.")

            # generate test cases from spec using real page data
            print(f"[SPEC] Generating test cases from requirements file ({len(spec_text)} chars, {len(real_fields)} real fields, {len(spec_sections)} sections)…")
            if emit_fn:
                emit_fn({"log": f"[SPEC] Génération depuis les exigences ({len(spec_text)} chars)…"})

            spec_cases = await asyncio.to_thread(
                planner.plan_from_spec, spec_text, real_fields, url, page_info_live, spec_sections
            )
            print(f"[SPEC] {len(spec_cases)} test cases generated from requirements.")
            if emit_fn:
                emit_fn({"log": f"[SPEC] {len(spec_cases)} tests générés depuis les exigences."})

            # run the test cases
            # Skip auth_flow — already executed in Phase 1
            # Deduplicate by description (case-insensitive) before executing
            seen_desc: set[str] = set()
            exec_cases: list[dict] = []
            for tc in spec_cases:
                if tc.get("type") == "auth_flow":
                    continue
                key = tc.get("description", "").strip().lower()
                if key and key in seen_desc:
                    logger.info("Skipping duplicate spec test: '%s'", tc.get("description", ""))
                    continue
                seen_desc.add(key)
                exec_cases.append(tc)

            url_tc_pairs: list[tuple[str, dict]] = [
                (tc.get("target_url") or url, tc) for tc in exec_cases
            ]

            spec_matrix: dict = {}
            if url_tc_pairs:
                print(f"[SPEC] Executing {len(url_tc_pairs)} spec tests × {len(browsers)} browser(s)…")
                if emit_fn:
                    emit_fn({"log": f"[SPEC] Exécution de {len(url_tc_pairs)} tests × {len(browsers)} navigateur(s)…"})
                spec_matrix = await run_all_browsers_multi_page(
                    url_tc_pairs, browsers, emit_fn,
                    credentials=credentials,
                    login_url=url,
                )

            # merge — exclude auth_matrix (spec already covers login scenarios)
            # auth_matrix is intentionally excluded — the spec file already contains
            # login/logout scenarios (TC-001, TC-002, TC-012…). Including auth_matrix
            # would duplicate those tests under AUTH_FLOW_001 / VALID_LOGIN IDs.
            matrix: dict = dict(spec_matrix)

        except Exception as exc:
            traceback.print_exc()
            return {"error": repr(exc)}

        return self._save_and_build_report(url, browsers, spec_cases, matrix)



    async def run(self, url: str, browser_type: str = "chromium", emit_fn=None) -> dict:
        """Single-browser convenience wrapper."""
        return await self.run_multi_browser(url, [browser_type], emit_fn)

    async def run_with_spec(
        self,
        url:          str,
        spec_text:    str,
        browser_type: str = "chromium",
        emit_fn=None,
    ) -> dict:
        """Single-browser spec+URL convenience wrapper."""
        return await self.run_multi_browser_with_spec(url, spec_text, [browser_type], emit_fn)
