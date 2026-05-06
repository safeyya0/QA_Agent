import os
import re
import asyncio
import logging
from datetime import datetime
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

class BrowserWrapper:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

    async def start(self, browser_type: str = "chromium"):
        self.playwright = await async_playwright().start()

        if browser_type == "firefox":
            self.browser = await self.playwright.firefox.launch(headless=True)
        elif browser_type == "webkit":
            self.browser = await self.playwright.webkit.launch(headless=True)
        elif browser_type == "chrome":
            self.browser = await self.playwright.chromium.launch(headless=True, channel="chrome")
        elif browser_type == "edge":
            self.browser = await self.playwright.chromium.launch(headless=True, channel="msedge")
        elif browser_type == "brave":
            brave_paths = [
                r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
                r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
                "/usr/bin/brave-browser",
                "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
            ]
            exe = next((p for p in brave_paths if os.path.exists(p)), None)
            if exe:
                self.browser = await self.playwright.chromium.launch(headless=True, executable_path=exe)
            else:
                logger.warning("Brave not found — falling back to Chromium")
                self.browser = await self.playwright.chromium.launch(headless=True)
        else:
            self.browser = await self.playwright.chromium.launch(headless=True)

        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()
        # Auto-accept browser dialogs (alert/confirm/prompt) — needed for delete confirmations
        self.page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))

    async def open_page(self, url: str):
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception:
            try:
                await self.page.goto(url, wait_until="load", timeout=20000)
            except Exception:
                pass  # proceed with whatever the page has rendered
        await asyncio.sleep(1)

    async def extract_inputs(self):
        # Wait for the framework to render inputs (handles Angular/React/Vue SPAs)
        for wait_ms in (3000, 5000, 8000):
            try:
                await self.page.wait_for_selector(
                    "input:not([type='hidden']):not([type='submit']):not([type='button'])",
                    timeout=wait_ms,
                    state="visible",
                )
                break
            except Exception:
                pass

        inputs = await self.page.query_selector_all("input, select, textarea")
        fields = []
        for inp in inputs:
            atype    = await inp.get_attribute("type")
            aname    = await inp.get_attribute("name")
            aid      = await inp.get_attribute("id")
            aplaceholder = await inp.get_attribute("placeholder")
            tag      = await inp.evaluate("el => el.tagName.toLowerCase()")
            if atype in ["hidden", "submit", "button"]:
                continue
            # Find the closest label text for this field
            label_text = ""
            try:
                if aid:
                    label_text = await self.page.eval_on_selector(
                        f"label[for='{aid}']", "el => el.innerText.trim()"
                    )
            except Exception:
                pass
            fields.append({
                "type":        tag if tag in ("select", "textarea") else (atype or "text"),
                "name":        aname,
                "id":          aid,
                "placeholder": aplaceholder,
                "label":       label_text,
            })
        return fields

    async def extract_page_context(self) -> dict:
        """Extract rich context about the current page for intelligent test generation."""
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        try:
            headings = await self.page.eval_on_selector_all(
                "h1, h2, h3",
                "els => els.slice(0,5).map(e => e.innerText.trim()).filter(t => t)"
            )
        except Exception:
            headings = []
        try:
            buttons = await self.page.eval_on_selector_all(
                "button, input[type='submit'], [type='button']",
                "els => els.slice(0,8).map(e => (e.innerText || e.value || '').trim()).filter(t => t)"
            )
        except Exception:
            buttons = []
        try:
            labels = await self.page.eval_on_selector_all(
                "label",
                "els => els.slice(0,15).map(e => e.innerText.trim()).filter(t => t)"
            )
        except Exception:
            labels = []
        try:
            selects = await self.page.eval_on_selector_all(
                "select",
                "els => els.map(s => ({name: s.name || s.id, options: [...s.options].slice(0,5).map(o=>o.text)}))"
            )
        except Exception:
            selects = []
        # Detect CRUD action links/buttons
        try:
            action_links = await self.page.eval_on_selector_all(
                "a, button",
                """els => els
                    .map(e => (e.innerText || e.textContent || e.value || '').trim())
                    .filter(t => t && t.length < 40)
                    .slice(0, 20)"""
            )
        except Exception:
            action_links = []

        # Detect table rows (suggests a list/CRUD page)
        try:
            row_count = await self.page.eval_on_selector_all(
                "table tr, [role='row']", "els => els.length"
            )
        except Exception:
            row_count = 0

        return {
            "title":        title,
            "headings":     headings,
            "buttons":      buttons,
            "labels":       labels,
            "selects":      selects,
            "action_links": action_links,
            "has_table":    row_count > 1,
            "row_count":    row_count,
        }

    async def fill_field(self, field_identifier: str, value: str):
        selectors = [
            f"input[name='{field_identifier}']",
            f"input[id='{field_identifier}']",
            f"input[placeholder*='{field_identifier}' i]",
            f"#{field_identifier}",
            f"input[type='{field_identifier}']",
        ]

        for sel in selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    await self.page.fill(sel, value, timeout=1000)
                    return
            except Exception:
                continue

        # Field not present on this page (e.g. multi-step form) — skip silently
        logger.debug("Field '%s' not found on page — skipped.", field_identifier)

    async def select_option(self, field_identifier: str, value: str):
        """Select a dropdown option by field name and option label/value."""
        selectors = [
            f"select[name='{field_identifier}']",
            f"select[id='{field_identifier}']",
            f"#{field_identifier}",
        ]
        for sel in selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    try:
                        await self.page.select_option(sel, label=value, timeout=2000)
                    except Exception:
                        await self.page.select_option(sel, value=value, timeout=2000)
                    return
            except Exception:
                continue
        logger.debug("Dropdown '%s' not found — skipped.", field_identifier)

    async def click_element(self, text_or_selector: str):
        """Click any element — by visible text, aria-label, title, or CSS selector."""
        t = text_or_selector
        candidates = [
            f"button:has-text('{t}')",
            f"a:has-text('{t}')",
            f"[aria-label*='{t}' i]",
            f"[title*='{t}' i]",
            f"input[value='{t}']",
            f"[role='button']:has-text('{t}')",
            f"li:has-text('{t}') a",
            t,  # treat as raw CSS selector
        ]
        for sel in candidates:
            try:
                loc = self.page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    await loc.click(timeout=4000)
                    # Wait for possible navigation or DOM update
                    try:
                        await self.page.wait_for_load_state("domcontentloaded", timeout=4000)
                    except Exception:
                        pass
                    await asyncio.sleep(0.8)
                    return
            except Exception:
                continue
        logger.debug("Element '%s' not found — skipped.", t)

    async def check_checkbox(self, field_identifier: str):
        """Check a checkbox by name, id, or label text."""
        selectors = [
            f"input[type='checkbox'][name='{field_identifier}']",
            f"input[type='checkbox'][id='{field_identifier}']",
            f"label:has-text('{field_identifier}') input[type='checkbox']",
        ]
        for sel in selectors:
            try:
                if await self.page.locator(sel).count() > 0:
                    await self.page.check(sel, timeout=2000)
                    return
            except Exception:
                continue
        logger.debug("Checkbox '%s' not found — skipped.", field_identifier)

    async def click_submit(self):
        """Click the most appropriate submit/action button on the current page.

        Strategy: check visibility first (instant), only attempt click on found
        elements — avoids the full timeout cost of missing selectors.
        """
        # Ordered from most specific to least specific.
        # Each group is tried in order; first visible match wins.
        submit_selectors = [
            # Standard submit inputs/buttons
            "button[type='submit']",
            "input[type='submit']",
            # Common action verbs (case-insensitive via :has-text)
            "button:has-text('Search')",
            "button:has-text('Login')",
            "button:has-text('Log in')",
            "button:has-text('Sign In')",
            "button:has-text('Sign Up')",
            "button:has-text('Register')",
            "button:has-text('Submit')",
            "button:has-text('Save')",
            "button:has-text('Confirm')",
            "button:has-text('Continue')",
            "button:has-text('Next')",
            "button:has-text('Send')",
            "button:has-text('Add')",
            "button:has-text('Create')",
            "button:has-text('Update')",
            "button:has-text('Apply')",
            # Role-based
            "[role='button'][type='submit']",
            "form button:not([type='button']):not([type='reset'])",
            # Class-based
            ".btn-primary",
            ".btn[type='submit']",
            "#submit",
            # Vue/custom framework: any visible button inside a form
            "form button",
            # Absolute last resort: any button that is visible
            "button:visible",
        ]

        for sel in submit_selectors:
            try:
                loc = self.page.locator(sel).first
                # count() is synchronous-like with no wait — instant check
                if await loc.count() > 0:
                    # Only pay the visibility timeout if the element actually exists
                    try:
                        visible = await loc.is_visible(timeout=800)
                    except Exception:
                        visible = False
                    if visible:
                        await loc.click(timeout=3000)
                        return
            except Exception:
                continue

        # Final fallback: Enter key on the active element
        try:
            await self.page.keyboard.press("Enter")
            return
        except Exception:
            pass

        raise Exception("Submit button not found.")

    async def wait_for_load(self):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(1)

    async def take_screenshot(self, name: str) -> str:
        """Save a screenshot with a unique timestamp suffix to prevent overwrites."""
        from config import SCREENSHOT_DIR
        os.makedirs(SCREENSHOT_DIR, exist_ok=True)
        ts   = datetime.now().strftime("%H%M%S_%f")[:10]
        path = os.path.join(SCREENSHOT_DIR, f"{name}_{ts}.png")
        await self.page.screenshot(path=path)
        return path

    async def get_page_text(self) -> str:
        return await self.page.locator("body").inner_text()

    async def get_page_url(self):
        return self.page.url

    async def fill_all_visible_fields(self, value_hint: str = "Test") -> int:
        """
        Dynamically fill every visible input/select/textarea on the current page.
        Also handles Vue.js / OrangeHRM custom dropdowns (oxd-select-text, combobox).
        Returns the number of fields filled.
        """
        import random
        suffix = str(random.randint(100, 999))
        inputs = await self.page.query_selector_all(
            "input:not([type='hidden']):not([type='submit']):not([type='button'])"
            ":not([type='reset']):not([type='image']), textarea, select"
        )
        filled = 0
        for inp in inputs:
            try:
                if not await inp.is_visible():
                    continue
                tag   = (await inp.evaluate("el => el.tagName.toLowerCase()"))
                ftype = ((await inp.get_attribute("type")) or "text").lower()
                fid   = (await inp.get_attribute("id")) or (await inp.get_attribute("name")) or ""

                if ftype in ("hidden", "submit", "button", "reset", "image"):
                    continue

                if tag == "select":
                    await inp.evaluate(
                        "el => { for(let i=1;i<el.options.length;i++){"
                        "if(el.options[i].value){el.selectedIndex=i;break;}}}"
                    )
                    filled += 1
                elif ftype == "checkbox":
                    checked = await inp.is_checked()
                    if not checked:
                        await inp.check()
                    filled += 1
                elif ftype == "radio":
                    await inp.check()
                    filled += 1
                elif ftype == "email":
                    await inp.fill(f"qa{suffix}@test.com")
                    filled += 1
                elif ftype == "password":
                    await inp.fill("TestPass123!")
                    filled += 1
                elif ftype in ("number", "range"):
                    await inp.fill("1")
                    filled += 1
                elif ftype == "date":
                    await inp.fill("2024-06-15")
                    filled += 1
                elif ftype in ("text", "search", "tel", "url", "") or tag == "textarea":
                    val = (value_hint or "Test")[:20]
                    cur = await inp.input_value()
                    if not cur:  # skip pre-filled fields
                        await inp.fill(val)
                    filled += 1
            except Exception:
                continue

        # Handle custom dropdown components (OrangeHRM oxd-select, Vue Select, ARIA combobox)
        try:
            custom_selects = await self.page.query_selector_all(
                "[role='combobox']:not(input), .oxd-select-text, .vs__dropdown-toggle"
            )
            for cs in custom_selects:
                try:
                    if not await cs.is_visible():
                        continue
                    inner = (await cs.inner_text()).strip().lower()
                    # Only interact when no real selection exists yet
                    if inner in ("-- select --", "select...", "please select", "select", ""):
                        await cs.click()
                        await asyncio.sleep(0.3)
                        clicked = False
                        for opt_sel in [
                            ".oxd-select-option:not(.oxd-select-option--disabled):first-of-type",
                            "[role='option']:not([aria-disabled='true']):first-child",
                            "li[class*='option']:first-child",
                            "ul[role='listbox'] li:first-child",
                            ".vs__dropdown-option:first-child",
                        ]:
                            try:
                                loc = self.page.locator(opt_sel).first
                                if await loc.count() > 0 and await loc.is_visible():
                                    await loc.click()
                                    filled += 1
                                    clicked = True
                                    await asyncio.sleep(0.2)
                                    break
                            except Exception:
                                continue
                        if not clicked:
                            try:
                                await self.page.keyboard.press("Escape")
                            except Exception:
                                pass
                except Exception:
                    continue
        except Exception:
            pass

        return filled

    async def has_success_message(self) -> bool:
        """Detect visible success/confirmation UI elements or text."""
        success_selectors = [
            "[class*='success' i]",
            "[class*='toast' i]",
            "[class*='alert-success' i]",
            "[class*='notification' i]",
            "[role='status']",
            "[class*='snackbar' i]",
        ]
        for sel in success_selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0 and await loc.first.is_visible():
                    return True
            except Exception:
                continue
        text = (await self.page.locator("body").inner_text()).lower()
        return bool(re.search(
            r'\b(successfully|success|saved|created|added|deleted|removed|updated|'
            r'modifi[eé]|ajout[eé]|supprim[eé]|enregistr[eé]|cr[eé][eé]|'
            r'mis à jour|opération réussie|changement sauvegardé)\b',
            text
        ))

    async def page_contains_text(self, text: str) -> bool:
        """Check if the given text (case-insensitive) appears anywhere on the page."""
        try:
            body = (await self.page.locator("body").inner_text()).lower()
            return text.lower() in body
        except Exception:
            return False

    async def has_error_message(self):
        # Check for error UI elements first (most reliable)
        error_selectors = [
            "[role='alert']",
            "[class*='error' i]",
            "[class*='alert' i]",
            "[class*='invalid' i]",
            "[id*='error' i]",
            ".form-error",
            ".field-error",
        ]
        for sel in error_selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    el = loc.first
                    if await el.is_visible():
                        # Require actual text content — avoids false positives from empty styled divs
                        txt = (await el.inner_text()).strip()
                        if txt and len(txt) > 2:
                            return True
            except Exception:
                continue
        # Fallback: look for specific auth error phrases only
        text = (await self.page.locator("body").inner_text()).lower()
        return bool(re.search(
            r'\b(invalid (password|credentials|email|username)|'
            r'incorrect (password|credentials)|'
            r'wrong (password|credentials)|'
            r'login (failed|error)|'
            r'authentication failed|'
            r'account (not found|does not exist)|'
            r'no account found)\b',
            text
        ))

    async def close(self):
        for obj, method in (
            (self.context,    "close"),
            (self.browser,    "close"),
            (self.playwright, "stop"),
        ):
            if obj:
                try:
                    await getattr(obj, method)()
                except Exception:
                    pass  # already closed by CTRL+C or crash — ignore
