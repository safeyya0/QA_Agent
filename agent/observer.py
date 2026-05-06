"""Observer: extracts ALL interactive elements from a page, not just form inputs."""
import logging
from typing import Any
from tools.browser import BrowserWrapper

logger = logging.getLogger(__name__)


class Observer:
    """Navigates to a URL and extracts every interactive element with its category."""

    def __init__(self, browser: BrowserWrapper) -> None:
        self.browser = browser

    async def observe(self, url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Return (elements, page_info) for the given URL.

        elements is a flat list of dicts, each with a 'category' key:
          'form_field'   — input / select / textarea
          'action'       — button / [role=button] / submit / a.btn
          'data_display' — table (headers + row count)
          'navigation'   — nav / sidebar / menu links
        """
        logger.info("Observing %s", url)
        await self.browser.open_page(url)
        await self.browser.wait_for_load()

        elements: list[dict[str, Any]] = []
        elements.extend(await self._extract_form_fields())
        elements.extend(await self._extract_buttons())
        elements.extend(await self._extract_tables())
        elements.extend(await self._extract_nav_links())

        page_info = await self._extract_page_info()
        logger.info(
            "Observed %d elements (%d fields, %d actions, %d tables, %d nav) at %s",
            len(elements),
            sum(1 for e in elements if e.get("category") == "form_field"),
            sum(1 for e in elements if e.get("category") == "action"),
            sum(1 for e in elements if e.get("category") == "data_display"),
            sum(1 for e in elements if e.get("category") == "navigation"),
            url,
        )
        return elements, page_info

    # ── Form fields ────────────────────────────────────────────────────────────

    async def _extract_form_fields(self) -> list[dict[str, Any]]:
        """Extract visible input / select / textarea elements."""
        page = self.browser.page
        results: list[dict[str, Any]] = []

        # SPA progressive wait
        for wait_ms in (2000, 4000, 7000):
            try:
                await page.wait_for_selector(
                    "input:not([type='hidden']):not([type='submit']):not([type='button'])",
                    timeout=wait_ms,
                    state="visible",
                )
                break
            except Exception:
                pass

        inputs = await page.query_selector_all("input, select, textarea")
        for inp in inputs:
            try:
                atype = (await inp.get_attribute("type") or "").lower()
                if atype in ("hidden", "submit", "button", "reset", "image"):
                    continue
                if not await inp.is_visible():
                    continue

                aname  = await inp.get_attribute("name") or ""
                aid    = await inp.get_attribute("id")   or ""
                apheld = await inp.get_attribute("placeholder") or ""
                areq   = await inp.get_attribute("required")
                tag    = await inp.evaluate("el => el.tagName.toLowerCase()")

                label_text = ""
                if aid:
                    try:
                        label_text = await page.eval_on_selector(
                            f"label[for='{aid}']",
                            "el => el.innerText.trim()",
                        )
                    except Exception:
                        pass

                results.append({
                    "category":    "form_field",
                    "tag":         tag,
                    "type":        tag if tag in ("select", "textarea") else (atype or "text"),
                    "name":        aname,
                    "id":          aid,
                    "placeholder": apheld,
                    "required":    areq is not None,
                    "label":       label_text,
                })
            except Exception:
                continue

        return results

    # ── Action buttons ─────────────────────────────────────────────────────────

    async def _extract_buttons(self) -> list[dict[str, Any]]:
        """Extract all clickable action elements."""
        page = self.browser.page
        results: list[dict[str, Any]] = []
        seen_texts: set[str] = set()

        for sel in (
            "button",
            "[role='button']",
            "input[type='submit']",
            "input[type='button']",
            "a.btn",
            "a[class*='button']",
            "a[class*='btn']",
        ):
            try:
                els = await page.query_selector_all(sel)
                for el in els:
                    try:
                        if not await el.is_visible():
                            continue
                        text = (
                            (await el.inner_text()).strip()
                            or (await el.get_attribute("value") or "")
                            or (await el.get_attribute("aria-label") or "")
                        )[:60]
                        if not text or text.lower() in seen_texts:
                            continue
                        seen_texts.add(text.lower())
                        results.append({
                            "category": "action",
                            "text":     text,
                            "id":       await el.get_attribute("id") or "",
                            "selector": sel,
                        })
                    except Exception:
                        continue
            except Exception:
                continue

        return results

    # ── Data tables ────────────────────────────────────────────────────────────

    async def _extract_tables(self) -> list[dict[str, Any]]:
        """Extract tables — both native <table> and ARIA/Vue.js div-based grids."""
        page = self.browser.page
        results: list[dict[str, Any]] = []

        # ── Native <table> elements ────────────────────────────────────────────
        try:
            for table in await page.query_selector_all("table"):
                try:
                    if not await table.is_visible():
                        continue
                    headers = await table.eval_on_selector_all(
                        "th",
                        "els => els.map(e => e.innerText.trim()).filter(t => t)",
                    )
                    row_count = len(await table.query_selector_all("tbody tr"))
                    results.append({
                        "category":  "data_display",
                        "tag":       "table",
                        "headers":   headers[:10],
                        "row_count": row_count,
                    })
                except Exception:
                    continue
        except Exception:
            pass

        # ── ARIA / Vue.js / custom grid tables (role="grid", role="table") ────
        # OrangeHRM, AG-Grid, Vue-Table, etc. use div-based grids with ARIA roles
        if not results:
            try:
                grid_data = await page.evaluate("""
                    () => {
                        const grids = document.querySelectorAll(
                            '[role="grid"], [role="table"], .oxd-table, '
                            + '.orangehrm-list-container, [class*="data-table"], '
                            + '[class*="datatable"], [class*="ag-root"]'
                        );
                        return [...grids].map(grid => {
                            const headerEls = grid.querySelectorAll(
                                '[role="columnheader"], th, .oxd-table-header-cell, '
                                + '[class*="header-cell"], [class*="col-header"]'
                            );
                            const rowEls = grid.querySelectorAll(
                                '[role="row"], tr, .oxd-table-row, '
                                + '[class*="table-row"]:not([class*="header"])'
                            );
                            const headers = [...headerEls]
                                .map(e => e.innerText.trim())
                                .filter(t => t && t.length < 60);
                            return {
                                headers: headers.slice(0, 10),
                                row_count: Math.max(0, rowEls.length - 1),
                                visible: grid.offsetParent !== null,
                            };
                        }).filter(g => g.visible && (g.headers.length > 0 || g.row_count > 0));
                    }
                """)
                for gd in grid_data:
                    results.append({
                        "category":  "data_display",
                        "tag":       "grid",
                        "headers":   gd.get("headers", []),
                        "row_count": gd.get("row_count", 0),
                    })
            except Exception:
                pass

        return results

    # ── Nav links ──────────────────────────────────────────────────────────────

    async def _extract_nav_links(self) -> list[dict[str, Any]]:
        """Extract navigation / sidebar / menu links, skipping logout links."""
        page = self.browser.page
        results: list[dict[str, Any]] = []
        seen_hrefs: set[str] = set()

        _LOGOUT_KWS = {"logout", "sign out", "log out", "signout", "se déconnecter"}

        nav_selector = (
            "nav a, .sidebar a, aside a, "
            "[class*='menu'] a, [class*='nav-item'] a, "
            "[role='navigation'] a, header a"
        )
        try:
            links = await page.query_selector_all(nav_selector)
            for link in links:
                try:
                    if not await link.is_visible():
                        continue
                    text = (await link.inner_text()).strip()[:60]
                    href = (await link.get_attribute("href") or "").strip()
                    if not text or not href:
                        continue
                    if href in ("#", "javascript:void(0)", "javascript:"):
                        continue
                    if text.lower() in _LOGOUT_KWS or any(kw in href for kw in ("logout", "signout")):
                        continue
                    if href in seen_hrefs:
                        continue
                    seen_hrefs.add(href)
                    results.append({
                        "category": "navigation",
                        "text":     text,
                        "href":     href,
                    })
                except Exception:
                    continue
        except Exception:
            pass

        return results[:30]

    # ── Page metadata ──────────────────────────────────────────────────────────

    async def _extract_page_info(self) -> dict[str, Any]:
        """Return title, current URL, and first 500 chars of body text."""
        page = self.browser.page
        try:
            title = await page.title()
        except Exception:
            title = ""
        try:
            page_text = await page.locator("body").inner_text()
        except Exception:
            page_text = ""

        return {
            "title":       title,
            "current_url": page.url,
            "page_text":   page_text,
        }
