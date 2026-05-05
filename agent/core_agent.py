import re
import json
import os
import asyncio
import traceback
from datetime import datetime
from urllib.parse import urlparse, urljoin
from agent.observer import Observer
from agent.planner import Planner
from agent.executor import run_all_browsers_parallel
from tools.browser import BrowserWrapper
from tools.trello import create_failure_card


class CoreAgent:

    async def _observe(self, url: str, browser_type: str = "chromium"):
        """Open URL with a temporary browser, return (fields, page_info)."""
        bw = BrowserWrapper()
        observer = Observer(bw)
        try:
            await bw.start(browser_type)
            fields, page_info = await observer.observe(url)
            return fields, page_info
        finally:
            await bw.close()

    def _save_and_build_report(self, url: str, browsers: list,
                                test_cases: list, matrix: dict,
                                plan_only: bool = False) -> dict:
        """Build the standard response dict, persist to JSON, return it."""
        total   = len(matrix)
        passed  = sum(1 for d in matrix.values() if d.get("overall") == "PASSED")
        failed  = sum(1 for d in matrix.values() if d.get("overall") == "FAILED")
        partial = sum(1 for d in matrix.values() if d.get("overall") == "PARTIAL")

        report = {
            "timestamp":    datetime.now().isoformat(),
            "url":          url,
            "browsers":     browsers,
            "multi_browser": len(browsers) > 1,
            "plan_only":    plan_only,
            "total_tests":  total,
            "passed":       passed,
            "failed":       failed,
            "partial":      partial,
            "results":      matrix,
        }

        os.makedirs("output", exist_ok=True)
        path = f"output/report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"[REPORT] Saved to {path}")
        print(f"[REPORT] Total: {total} | Passed: {passed} | Failed: {failed} | Partial: {partial}")

        # Trello cards for failures
        for tid, data in matrix.items():
            if data.get("overall") in ("FAILED", "PARTIAL"):
                failed_browsers = [
                    b for b, bd in data.get("browsers", {}).items()
                    if bd.get("status") not in ("PASSED", "SKIPPED")
                ]
                error_str  = " | ".join(
                    f"{b}: {data['browsers'][b].get('error','failed')}" for b in failed_browsers
                )
                screenshot = next(
                    (data["browsers"][b].get("screenshot") for b in browsers
                     if b in data.get("browsers", {}) and data["browsers"][b].get("screenshot")),
                    None,
                )
                create_failure_card(
                    test_id=tid,
                    error_details=error_str or "Test failed",
                    screenshot_path=screenshot,
                    description=data.get("description", ""),
                )

        report["report_file"] = os.path.basename(path)
        return report

    # ── Credential helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _find_valid_credentials(test_cases: list) -> dict | None:
        """Extract login credentials from the test case marked as valid login."""
        for tc in test_cases:
            expected = (tc.get("expected") or "").lower()
            if (re.search(r'\b(success|redirect|dashboard|inventory|home|welcome|logged)\b', expected)
                    and not re.search(r'\b(fail|error|invalid|wrong)\b', expected)):
                creds = {}
                for step in tc.get("steps", []):
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
        text = page_text
        username = None
        password = None

        _bad_words = {"password", "pass", "pwd", "login", "email", "enter",
                      "your", "the", "a", "username", "user", "name"}

        # Ordered from most specific to least — stops on first valid hit
        username_patterns = [
            r'accepted usernames?(?:\s+are)?[:\s]+([a-zA-Z0-9_.\-]+)',
            r'(?:demo|sample|test|example|default)\s+(?:username|user|login)[:\s]+([a-zA-Z0-9_.\-]+)',
            r'(?:username|user|login):\s*([a-zA-Z0-9_.\-]{3,30})',
        ]
        for pat in username_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if candidate.lower() not in _bad_words and len(candidate) >= 3:
                    username = candidate
                    break

        password_patterns = [
            r'password\s+for\s+(?:all\s+)?users?[:\s]+([a-zA-Z0-9_@!#$%^&*()\-]{4,})',
            r'(?:password|pass|pwd):\s*([a-zA-Z0-9_@!#$%^&*()\-]{4,})',
        ]
        for pat in password_patterns:
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                candidate = m.group(1).strip()
                if candidate.lower() not in _bad_words and len(candidate) >= 4:
                    password = candidate
                    break

        if username and password:
            print(f"[EXTRACT] Page credentials found: username='{username}', password='{password}'")
            return {"username": username, "password": password}
        return None

    # ── Platform exploration ───────────────────────────────────────────────────

    async def _explore_platform(self, url: str, browser_type: str,
                                 credentials: dict, emit_fn=None) -> dict:
        """
        Login with known credentials, then crawl all internal navigation links.
        For each discovered page: screenshot + check errors + test any forms found.
        """
        from tools.llm import generate_test_cases as llm_gen
        from agent.executor import Executor

        base_domain = urlparse(url).netloc.removeprefix("www.")
        bw = BrowserWrapper()
        matrix: dict = {}

        try:
            await bw.start(browser_type)

            # ── Step 1: Login ──────────────────────────────────────────────────
            print(f"[EXPLORE] Logging in to {url} as '{credentials['username']}'")
            await bw.open_page(url)
            fields = await bw.extract_inputs()

            for field in fields:
                fid   = (field.get("id") or field.get("name") or "").lower()
                ftype = (field.get("type") or "").lower()
                ident = field.get("id") or field.get("name") or ftype
                if any(k in fid for k in ("user", "email", "login", "name")) or ftype == "text":
                    await bw.fill_field(ident, credentials["username"])
                elif ftype == "password":
                    await bw.fill_field(ident, credentials["password"])

            await bw.click_submit()
            try:
                await bw.page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await asyncio.sleep(2)

            post_login_url = await bw.get_page_url()
            origin_path = urlparse(url).path.rstrip("/") or "/"
            post_path   = urlparse(post_login_url).path.rstrip("/") or "/"
            print(f"[EXPLORE] Post-login URL: {post_login_url} (path: {post_path})")
            if post_path == origin_path:
                print("[EXPLORE] Login failed — URL did not change. Credentials may be wrong.")
                return {}

            print(f"[EXPLORE] Login successful — now at {post_login_url}")
            if emit_fn:
                emit_fn({"log": f"[EXPLORE] Login successful — exploring platform interior..."})

            # ── Step 2: Discover nav links ─────────────────────────────────────
            raw_links: list = await bw.page.evaluate("""
                () => {
                    const sel = 'a[href], nav a, .nav a, .menu a, header a, [role="navigation"] a, .sidebar a';
                    return [...new Set([...document.querySelectorAll(sel)].map(a => a.href))]
                        .filter(h => h && !h.startsWith('javascript:') && !h.includes('#')
                                   && !h.match(/\\.(pdf|zip|jpg|png|gif|svg|css|js)$/i));
                }
            """)

            internal_links = [
                l for l in raw_links
                if urlparse(l).netloc.removeprefix("www.") == base_domain and l != post_login_url
            ]
            # Deduplicate and cap at 10 pages
            seen = set()
            unique_links = []
            for l in internal_links:
                norm = urlparse(l)._replace(fragment="", query="").geturl()
                if norm not in seen:
                    seen.add(norm)
                    unique_links.append(l)
            unique_links = unique_links[:10]

            print(f"[EXPLORE] Raw links found: {len(raw_links)}, internal unique: {len(unique_links)}")
            for lnk in unique_links:
                print(f"[EXPLORE]   -> {lnk}")

            # ── Step 3: Visit each page ────────────────────────────────────────
            executor = Executor(bw)

            for i, page_url in enumerate(unique_links, start=1):
                tc_id = f"EXPLORE_{i:03d}"
                path_label = urlparse(page_url).path.rstrip("/") or "/"
                print(f"[EXPLORE] ({i}/{len(unique_links)}) {page_url}")
                if emit_fn:
                    emit_fn({"log": f"[EXPLORE] Visiting {path_label}"})

                entry = {
                    "title":       path_label,
                    "description": f"Page exploration: {path_label}",
                    "expected":    "Page loads without errors",
                    "steps":       [],
                    "type":        "exploration",
                    "browsers":    {},
                    "overall":     "PASSED",
                }

                try:
                    await bw.open_page(page_url)
                    await bw.wait_for_load()

                    has_error  = await bw.has_error_message()
                    screenshot = await bw.take_screenshot(f"explore_{tc_id}")
                    page_text  = await bw.get_page_text()
                    status     = "FAILED" if has_error else "PASSED"

                    entry["browsers"][browser_type] = {
                        "status":     status,
                        "error":      "Error message detected on page" if has_error else "",
                        "screenshot": screenshot,
                    }
                    entry["overall"] = status

                    # If forms found on this page, generate + run quick tests
                    page_fields = await bw.extract_inputs()
                    if page_fields:
                        print(f"[EXPLORE] Found {len(page_fields)} form field(s) on {path_label} — running quick tests")
                        if emit_fn:
                            emit_fn({"log": f"[EXPLORE] Testing form on {path_label}..."})
                        try:
                            sub_cases = llm_gen(page_fields, page_url, page_text=page_text[:400])
                            for j, tc in enumerate(sub_cases[:4], start=1):
                                sub_id = f"EXPLORE_{i:03d}_F{j:02d}"
                                tc["id"] = sub_id
                                res = await executor.execute(tc, page_url)
                                sub_status = (res.get("status") or "unknown").upper()
                                matrix[sub_id] = {
                                    "title":       tc.get("description", ""),
                                    "description": tc.get("description", ""),
                                    "expected":    tc.get("expected", ""),
                                    "steps":       tc.get("steps", []),
                                    "type":        "exploration_form",
                                    "browsers":    {browser_type: {
                                        "status":     sub_status,
                                        "error":      res.get("error") or "",
                                        "screenshot": res.get("screenshot") or "",
                                    }},
                                    "overall": sub_status,
                                }
                                if emit_fn:
                                    emit_fn({"test_id": sub_id, "browser": browser_type,
                                             "status": sub_status})
                        except Exception as e:
                            print(f"[EXPLORE] Form test generation failed on {path_label}: {e}")

                except Exception as e:
                    print(f"[EXPLORE] Failed to visit {page_url}: {e}")
                    entry["browsers"][browser_type] = {
                        "status": "FAILED", "error": str(e), "screenshot": None,
                    }
                    entry["overall"] = "FAILED"

                matrix[tc_id] = entry
                if emit_fn:
                    emit_fn({"test_id": tc_id, "browser": browser_type,
                             "status": entry["overall"]})

        except Exception as e:
            traceback.print_exc()
            print(f"[EXPLORE] Exploration engine error: {e}")
        finally:
            await bw.close()

        print(f"[EXPLORE] Done — {len(matrix)} exploration result(s) added to report.")
        return matrix

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run_multi_browser(self, url: str, browsers: list,
                                 emit_fn=None) -> dict:
        if not browsers:
            browsers = ["chromium"]

        try:
            fields, page_info = await self._observe(url, browsers[0])
        except Exception as e:
            traceback.print_exc()
            return {"error": repr(e)}

        if not fields:
            return {"error": "No input fields found on the page."}

        page_text = page_info.get("page_text", "")
        planner   = Planner()
        test_cases = planner.plan(fields, url, page_info)
        print(f"[MULTI] {len(test_cases)} tests × {len(browsers)} browser(s) — running in parallel")

        matrix = await run_all_browsers_parallel(test_cases, url, browsers, emit_fn)

        # Platform exploration: login then crawl interior pages
        creds_from_page = self._extract_credentials_from_page(page_text)
        creds_from_cases = self._find_valid_credentials(test_cases)
        credentials = creds_from_page or creds_from_cases
        print(f"[EXPLORE] Credential sources — page_regex: {creds_from_page}, test_cases: {creds_from_cases}")
        if credentials:
            print(f"[EXPLORE] Using credentials: username='{credentials['username']}' — starting exploration...")
            explore = await self._explore_platform(url, browsers[0], credentials, emit_fn)
            matrix.update(explore)
        else:
            print("[EXPLORE] No valid credentials detected — skipping interior exploration.")

        return self._save_and_build_report(url, browsers, test_cases, matrix)

    async def run_multi_browser_with_spec(self, url: str, spec_text: str,
                                           browsers: list, emit_fn=None) -> dict:
        if not browsers:
            browsers = ["chromium"]

        has_url  = bool(url and url.strip())
        fields, page_info = [], {}

        if has_url:
            try:
                fields, page_info = await self._observe(url, browsers[0])
            except Exception as e:
                traceback.print_exc()
                return {"error": repr(e)}

        page_text = page_info.get("page_text", "")
        planner   = Planner()
        test_cases = planner.plan_from_spec(spec_text, fields, url, page_info)

        if not has_url:
            matrix = {
                tc["id"]: {
                    "title":       tc.get("description", ""),
                    "description": tc.get("description", ""),
                    "expected":    tc.get("expected", ""),
                    "steps":       tc.get("steps", []),
                    "type":        tc.get("type", "standard"),
                    "browsers":    {},
                    "overall":     "PLANNED",
                }
                for tc in test_cases
            }
            return self._save_and_build_report(url or "", browsers, test_cases, matrix, plan_only=True)

        print(f"[MULTI-SPEC] {len(test_cases)} spec tests × {len(browsers)} browser(s)")
        matrix = await run_all_browsers_parallel(test_cases, url, browsers, emit_fn)

        # Exploration after spec tests too
        creds_from_page  = self._extract_credentials_from_page(page_text)
        creds_from_cases = self._find_valid_credentials(test_cases)
        credentials = creds_from_page or creds_from_cases
        print(f"[EXPLORE] Credential sources — page_regex: {creds_from_page}, test_cases: {creds_from_cases}")
        if credentials and has_url:
            print(f"[EXPLORE] Credentials found — starting platform exploration...")
            explore = await self._explore_platform(url, browsers[0], credentials, emit_fn)
            matrix.update(explore)

        return self._save_and_build_report(url, browsers, test_cases, matrix)

    # ── Backward-compat single-browser wrappers ────────────────────────────────

    async def run(self, url: str, browser_type: str = "chromium",
                  emit_fn=None) -> dict:
        return await self.run_multi_browser(url, [browser_type], emit_fn)

    async def run_with_spec(self, url: str, spec_text: str,
                             browser_type: str = "chromium", emit_fn=None) -> dict:
        return await self.run_multi_browser_with_spec(url, spec_text, [browser_type], emit_fn)
