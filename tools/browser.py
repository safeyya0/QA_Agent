import os
import re
import asyncio
from playwright.async_api import async_playwright
from pathlib import Path

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
                print("[BROWSER] Brave not found — falling back to Chromium")
                self.browser = await self.playwright.chromium.launch(headless=True)
        else:
            self.browser = await self.playwright.chromium.launch(headless=True)

        self.context = await self.browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
        self.page = await self.context.new_page()

    async def open_page(self, url: str):
        try:
            await self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            await self.page.goto(url, wait_until="load", timeout=60000)
        await asyncio.sleep(2)

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

        inputs = await self.page.query_selector_all("input")
        fields = []
        for inp in inputs:
            atype = await inp.get_attribute("type")
            aname = await inp.get_attribute("name")
            aid = await inp.get_attribute("id")
            aplaceholder = await inp.get_attribute("placeholder")
            if atype not in ["hidden", "submit", "button"]:
                fields.append({
                    "type": atype,
                    "name": aname,
                    "id": aid,
                    "placeholder": aplaceholder
                })
        return fields

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
        print(f"[BROWSER] Field '{field_identifier}' not found on page — skipped.")

    async def click_submit(self):
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Submit')",
            "button:has-text('Login')",
            "button:has-text('Log in')",
            "button:has-text('Sign In')",
            "button:has-text('Sign Up')",
            "button:has-text('Register')",
            "button:has-text('Continue')",
            "button:has-text('Next')",
            "[role='button'][type='submit']",
            "form button:not([type='button'])",
            ".btn[type='submit']",
            ".btn-primary",
            "#submit",
        ]

        for sel in submit_selectors:
            try:
                loc = self.page.locator(sel)
                if await loc.count() > 0:
                    await loc.first.click(timeout=2000)
                    return
            except Exception:
                continue

        raise Exception("Submit button not found.")

    async def wait_for_load(self):
        try:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        await asyncio.sleep(1)

    async def take_screenshot(self, name: str):
        os.makedirs("output/screenshots", exist_ok=True)
        path = f"output/screenshots/{name}.png"
        await self.page.screenshot(path=path)
        return path

    async def get_page_text(self) -> str:
        return await self.page.locator("body").inner_text()

    async def get_page_url(self):
        return self.page.url

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
                    visible = await loc.first.is_visible()
                    if visible:
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
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
