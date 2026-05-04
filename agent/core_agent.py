import json
import os
import asyncio
import traceback
from datetime import datetime
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

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run_multi_browser(self, url: str, browsers: list,
                                 emit_fn=None) -> dict:
        """Observe once, plan once, then execute on all browsers in parallel."""
        if not browsers:
            browsers = ["chromium"]

        try:
            fields, page_info = await self._observe(url, browsers[0])
        except Exception as e:
            traceback.print_exc()
            return {"error": repr(e)}

        if not fields:
            return {"error": "No input fields found on the page."}

        planner    = Planner()
        test_cases = planner.plan(fields, url, page_info)
        print(f"[MULTI] {len(test_cases)} tests × {len(browsers)} browser(s) — running in parallel")

        matrix = await run_all_browsers_parallel(test_cases, url, browsers, emit_fn)
        return self._save_and_build_report(url, browsers, test_cases, matrix)

    async def run_multi_browser_with_spec(self, url: str, spec_text: str,
                                           browsers: list, emit_fn=None) -> dict:
        """Plan from spec, execute on all browsers (or return plan-only if no URL)."""
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

        planner    = Planner()
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
        return self._save_and_build_report(url, browsers, test_cases, matrix)

    # ── Backward-compat single-browser wrappers ────────────────────────────────

    async def run(self, url: str, browser_type: str = "chromium",
                  emit_fn=None) -> dict:
        return await self.run_multi_browser(url, [browser_type], emit_fn)

    async def run_with_spec(self, url: str, spec_text: str,
                             browser_type: str = "chromium", emit_fn=None) -> dict:
        return await self.run_multi_browser_with_spec(url, spec_text, [browser_type], emit_fn)
