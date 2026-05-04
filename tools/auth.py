import re
from uuid import uuid4
from urllib.parse import urljoin


def generate_test_credentials() -> dict:
    uid = uuid4().hex
    return {
        "email": f"test_{uid[:8]}@testmail.com",
        "password": f"Test@{uid[8:16]}1A",
        "username": f"testuser_{uid[:6]}"
    }


def detect_auth_forms(fields: list, url: str, page_text: str = "") -> dict:
    url_lower = url.lower()
    text_lower = page_text.lower()

    field_ids = [(f.get("id") or "").lower() for f in fields]
    field_names = [(f.get("name") or "").lower() for f in fields]
    field_types = [(f.get("type") or "").lower() for f in fields]
    all_identifiers = field_ids + field_names

    has_password = "password" in field_types
    has_email = "email" in field_types or any("email" in i for i in all_identifiers)
    has_confirm_password = any("confirm" in i for i in all_identifiers)
    has_username = any("username" in i or "user_name" in i for i in all_identifiers)

    register_url_signal = any(kw in url_lower for kw in ["register", "signup", "sign-up", "create-account", "join"])
    login_url_signal = any(kw in url_lower for kw in ["login", "signin", "sign-in", "auth"])

    register_text_signal = any(kw in text_lower for kw in ["create account", "sign up", "register", "join us", "get started", "create your account"])
    login_text_signal = any(kw in text_lower for kw in ["sign in", "log in", "login", "welcome back"])

    has_register = register_url_signal or has_confirm_password or (register_text_signal and not login_url_signal)
    has_login = login_url_signal or (has_password and has_email and not has_register) or (login_text_signal and not has_register)

    login_url = url if has_login else _derive_login_url(url)
    register_url = url if has_register else None

    return {
        "has_login": has_login,
        "has_register": has_register,
        "login_url": login_url,
        "register_url": register_url,
        "has_username_field": has_username,
        "fields_detected": fields
    }


def _derive_login_url(register_url: str) -> str:
    replacements = [
        (r'register', 'login'),
        (r'signup', 'login'),
        (r'sign[-_]?up', 'login'),
        (r'create[-_]?account', 'login'),
        (r'join', 'login'),
    ]
    for pattern, replacement in replacements:
        new_url = re.sub(pattern, replacement, register_url, flags=re.IGNORECASE)
        if new_url != register_url:
            return new_url
    # fallback: append /login relative to origin
    from urllib.parse import urlparse
    parsed = urlparse(register_url)
    return f"{parsed.scheme}://{parsed.netloc}/login"


async def find_and_click_logout(page) -> dict:
    logout_selectors = [
        "button:has-text('Logout')",
        "button:has-text('Log out')",
        "button:has-text('Sign out')",
        "a:has-text('Logout')",
        "a:has-text('Log out')",
        "a:has-text('Sign out')",
        "[href*='logout']",
        "[href*='signout']",
        "[href*='sign-out']",
        "[data-testid*='logout']",
        "#logout",
        ".logout",
    ]

    for sel in logout_selectors:
        try:
            if await page.locator(sel).count() > 0:
                await page.click(sel, timeout=2000)
                return {"clicked": True, "selector": sel}
        except Exception:
            continue

    return {"clicked": False, "selector": None}


async def find_login_link(page, current_url: str) -> str | None:
    link_selectors = [
        "a:has-text('Login')",
        "a:has-text('Log in')",
        "a:has-text('Sign in')",
        "a[href*='login']",
        "a[href*='signin']",
        "a[href*='sign-in']",
    ]

    for sel in link_selectors:
        try:
            locator = page.locator(sel)
            if await locator.count() > 0:
                href = await locator.first.get_attribute("href")
                if href:
                    return href if href.startswith("http") else urljoin(current_url, href)
        except Exception:
            continue

    return None
