class Observer:
    def __init__(self, browser_wrapper):
        self.browser = browser_wrapper

    async def observe(self, url: str) -> tuple:
        print(f"[OBSERVE] Navigating to {url}")
        await self.browser.open_page(url)
        fields = await self.browser.extract_inputs()
        page_text = await self.browser.get_page_text()
        current_url = await self.browser.get_page_url()
        print(f"[OBSERVE] Detected {len(fields)} fields at {current_url}")
        return fields, {"page_text": page_text, "current_url": current_url}
