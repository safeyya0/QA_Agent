import os
import requests
from dotenv import load_dotenv

load_dotenv()

_KEY     = os.getenv("TRELLO_API_KEY", "")
_TOKEN   = os.getenv("TRELLO_TOKEN", "")
_LIST_ID = os.getenv("TRELLO_LIST_ID", "")
_BASE    = "https://api.trello.com/1"


def is_configured() -> bool:
    return bool(_KEY and _TOKEN and _LIST_ID)


def create_failure_card(test_id: str, error_details: str,
                        screenshot_path: str = None, description: str = "") -> dict:
    if not is_configured():
        print(f"[TRELLO MOCK] Creating card for {test_id}")
        print(f"  - Error: {error_details}")
        if screenshot_path:
            print(f"  - Attached image: {screenshot_path}")
        return {"card_id": "mock", "status": "mock"}

    name = f"[FAIL] {test_id}"
    desc = f"**Test:** {description}\n\n**Error:**\n{error_details}"

    try:
        resp = requests.post(
            f"{_BASE}/cards",
            params={"key": _KEY, "token": _TOKEN},
            json={"idList": _LIST_ID, "name": name, "desc": desc, "pos": "top"},
            timeout=10,
        )
        resp.raise_for_status()
        card = resp.json()
        card_id = card["id"]
        print(f"[TRELLO] Card created: {card.get('shortUrl')}")

        if screenshot_path and os.path.exists(screenshot_path):
            with open(screenshot_path, "rb") as f:
                requests.post(
                    f"{_BASE}/cards/{card_id}/attachments",
                    params={"key": _KEY, "token": _TOKEN},
                    files={"file": (os.path.basename(screenshot_path), f, "image/png")},
                    timeout=15,
                )
            print(f"[TRELLO] Screenshot attached to {card_id}")

        return {"card_id": card_id, "status": "created", "url": card.get("shortUrl")}

    except Exception as e:
        print(f"[TRELLO] Error creating card: {e}")
        return {"card_id": None, "status": "error", "error": str(e)}
