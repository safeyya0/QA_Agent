import os
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

_KEY     = os.getenv("TRELLO_API_KEY", "")
_TOKEN   = os.getenv("TRELLO_TOKEN", "")
_LIST_ID = os.getenv("TRELLO_LIST_ID", "")
_BASE    = "https://api.trello.com/1"

# Cached board ID resolved from TRELLO_LIST_ID at first use
_board_id_cache: str | None = None


def is_configured() -> bool:
    return bool(_KEY and _TOKEN and _LIST_ID)


def _get_board_id() -> str | None:
    """Resolve the full 24-char board ID from TRELLO_LIST_ID (cached)."""
    global _board_id_cache
    if _board_id_cache:
        return _board_id_cache
    if not (_KEY and _TOKEN and _LIST_ID):
        return None
    try:
        resp = requests.get(
            f"{_BASE}/lists/{_LIST_ID}/board",
            params={"key": _KEY, "token": _TOKEN, "fields": "id,name"},
            timeout=10,
        )
        resp.raise_for_status()
        board = resp.json()
        _board_id_cache = board.get("id")
        print(f"[TRELLO] Board resolved: {board.get('name')} ({_board_id_cache})")
        return _board_id_cache
    except Exception as exc:
        print(f"[TRELLO] Could not resolve board from list {_LIST_ID}: {exc}")
        return None


def create_run_list(run_url: str = "") -> str | None:
    """Create a fresh Trello list for this test run and return its ID.

    The list name includes the timestamp and optionally the tested URL so
    each run is clearly identified on the board.
    Called automatically at the start of every run — no manual input needed.
    """
    if not (_KEY and _TOKEN):
        return None

    board_id = _get_board_id()
    if not board_id:
        print("[TRELLO] Cannot create run list — board ID unavailable.")
        return None

    label = run_url.replace("https://", "").replace("http://", "").split("/")[0]
    ts    = datetime.now().strftime("%Y-%m-%d %H:%M")
    name  = f"OMNISHORE QA - {label} - {ts}" if label else f"OMNISHORE QA - {ts}"

    try:
        resp = requests.post(
            f"{_BASE}/lists",
            params={
                "key": _KEY, "token": _TOKEN,
                "name": name, "idBoard": board_id, "pos": "top",
            },
            timeout=10,
        )
        resp.raise_for_status()
        lst = resp.json()
        print(f"[TRELLO] Run list created: '{name}' (id={lst['id']})")
        return lst["id"]
    except Exception as exc:
        print(f"[TRELLO] Failed to create run list: {exc}")
        return None


def create_failure_card(
    test_id: str,
    error_details: str,
    screenshot_path: str = None,
    description: str = "",
    list_id: str | None = None,
) -> dict:
    """Create a Trello card for a failed/partial test.

    Args:
        list_id: the run list ID returned by create_run_list().
                 Falls back to TRELLO_LIST_ID from .env if not provided.
    """
    target_list = list_id or _LIST_ID

    if not (_KEY and _TOKEN and target_list):
        print(f"[TRELLO MOCK] {test_id}: {error_details[:80]}")
        return {"card_id": "mock", "status": "mock"}

    name = f"[FAIL] {test_id}"
    desc = f"**Test:** {description}\n\n**Error:**\n{error_details}"

    try:
        resp = requests.post(
            f"{_BASE}/cards",
            params={
                "key": _KEY, "token": _TOKEN,
                "idList": target_list, "name": name, "desc": desc, "pos": "top",
            },
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

    except Exception as exc:
        print(f"[TRELLO] Error creating card: {exc}")
        return {"card_id": None, "status": "error", "error": str(exc)}
