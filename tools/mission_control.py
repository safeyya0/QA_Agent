"""
Mission Control bridge for OMNISHORE QA Agent.

Connection flow:
  1. POST /api/auth/login      → obtain session cookie
  2. POST /api/connect         → obtain connection_id, agent_id, sse_url, heartbeat_url
  3. Listen SSE /api/events    → real-time task assignments
  4. Poll GET heartbeat        → fallback every 20s
  5. PUT /api/tasks/{id}       → report results

Environment variables:
  MISSION_CONTROL_URL   — e.g. http://localhost:3000  (required to enable)
  MISSION_CONTROL_USER  — admin username               (required)
  MISSION_CONTROL_PASS  — admin password               (required)
"""
import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MC_URL  = (os.getenv("MISSION_CONTROL_URL") or "").rstrip("/")
MC_USER = os.getenv("MISSION_CONTROL_USER", "admin")
MC_PASS = os.getenv("MISSION_CONTROL_PASS", "")

# ── Shared state ───────────────────────────────────────────────────────────────
_session_cookie: str | None = None   # mc_session=... cookie value
_connection_id:  str | None = None
_agent_id:       int | None = None
_heartbeat_url:  str | None = None
_sse_url:        str | None = None

_processed: set[int] = set()


def _auth_headers() -> dict:
    h = {"Content-Type": "application/json"}
    if _session_cookie:
        h["Cookie"] = _session_cookie
    return h


# ── 1. Login — get session cookie ─────────────────────────────────────────────

async def login() -> bool:
    """POST /api/auth/login and store the session cookie."""
    global _session_cookie
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MC_URL}/api/auth/login",
                json={"username": MC_USER, "password": MC_PASS},
                headers={"Content-Type": "application/json"},
            )
            resp.raise_for_status()
            # Extract session cookie from Set-Cookie header
            cookie = resp.headers.get("set-cookie", "")
            # Keep only the key=value part (before first ;)
            for part in cookie.split(","):
                part = part.strip()
                if "mc_session" in part or "session" in part.lower():
                    _session_cookie = part.split(";")[0].strip()
                    break
            if not _session_cookie:
                # Fallback: use all cookies
                _session_cookie = "; ".join(
                    p.split(";")[0].strip()
                    for p in cookie.split(",")
                    if p.strip()
                )
        print(f"[MC Bridge] Logged in as '{MC_USER}'")
        return True
    except Exception as exc:
        print(f"[MC Bridge] login() failed: {exc}")
        return False


# ── 2. Bridge connection ───────────────────────────────────────────────────────

async def connect() -> bool:
    """POST /api/connect with session cookie. Populates module-level state."""
    global _connection_id, _agent_id, _heartbeat_url, _sse_url

    payload = {
        "tool_name":    "omnishore-qa",
        "agent_name":   "omnishore-qa",
        "agent_role":   "QA agent — automated browser testing on web applications",
        "tool_version": "1.0.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MC_URL}/api/connect",
                headers=_auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        _connection_id = data.get("connection_id")
        _agent_id      = data.get("agent_id")
        _heartbeat_url = f"{MC_URL}{data.get('heartbeat_url', f'/api/agents/{_agent_id}/heartbeat')}"
        _sse_url       = f"{MC_URL}{data.get('sse_url', '/api/events')}"

        print(f"[MC Bridge] Connected — connection_id={_connection_id}  agent_id={_agent_id}")
        return True
    except Exception as exc:
        print(f"[MC Bridge] connect() failed: {exc}")
        return False


async def disconnect() -> None:
    if not _connection_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.delete(
                f"{MC_URL}/api/connect",
                headers=_auth_headers(),
                params={"connection_id": _connection_id},
            )
        print(f"[MC Bridge] Disconnected (connection_id={_connection_id})")
    except Exception as exc:
        logger.debug("[MC Bridge] disconnect() error: %s", exc)


# ── 3. Poll heartbeat ─────────────────────────────────────────────────────────

async def poll_assigned_tasks() -> list[dict]:
    if not _heartbeat_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_heartbeat_url, headers=_auth_headers())
            resp.raise_for_status()
            data = resp.json()
        work_items = data.get("work_items") or []
        tasks = []
        for bucket in work_items:
            if bucket.get("type") == "assigned_tasks":
                tasks.extend(bucket.get("items") or [])
        return tasks
    except Exception as exc:
        logger.debug("[MC Bridge] heartbeat error: %s", exc)
        return []


# ── 4. Update task ─────────────────────────────────────────────────────────────

async def update_task(task_id: int, status: str, result: dict | None = None) -> bool:
    payload: dict = {"status": status}
    if result is not None:
        word = result.get("word_report", "")
        payload["result"] = (
            f"PASSED:{result.get('passed',0)}  "
            f"FAILED:{result.get('failed',0)}  "
            f"PARTIAL:{result.get('partial',0)}  "
            f"/ {result.get('total_tests',0)} tests"
            + (f"  — rapport: {word}" if word else "")
        )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{MC_URL}/api/tasks/{task_id}",
                headers=_auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("[MC Bridge] update_task(%s) error: %s", task_id, exc)
        return False


# ── 5. SSE listener ───────────────────────────────────────────────────────────

async def _listen_sse(on_task) -> None:
    if not _sse_url:
        return
    print(f"[MC Bridge] SSE listener → {_sse_url}")
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", _sse_url,
                headers={**_auth_headers(), "Accept": "text/event-stream"},
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "ping":
                        continue
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if (
                        event.get("type") in ("task_assigned", "assigned_tasks", "task_updated")
                        and event.get("agent_id") == _agent_id
                    ):
                        task = event.get("data") or event.get("task") or event
                        await on_task(task)
    except Exception as exc:
        logger.warning("[MC Bridge] SSE stream error: %s", exc)


# ── 6. Task execution ─────────────────────────────────────────────────────────

def _parse_task_params(task: dict) -> tuple[str, str, list[str]]:
    raw = task.get("description") or task.get("title") or task.get("name") or ""
    try:
        params = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        params = {"url": raw.strip()}
    url  = params.get("url", "")
    spec = params.get("spec", "")
    # If spec looks like a file path, read it
    if spec and not spec.startswith("#") and len(spec) < 260:
        import pathlib
        p = pathlib.Path(spec)
        if p.suffix in (".md", ".txt") and p.exists():
            spec = p.read_text(encoding="utf-8")
            print(f"[MC Bridge] Loaded spec from {p}")
    browsers = [b.strip() for b in params.get("browsers", "chromium").split(",") if b.strip()]
    return url, spec, browsers


async def _execute_task(task: dict, core_agent) -> None:
    task_id = task.get("id")
    if not task_id or task_id in _processed:
        return
    _processed.add(task_id)

    url, spec, browsers = _parse_task_params(task)
    if not url:
        print(f"[MC Bridge] Task {task_id} has no URL — marking failed.")
        await update_task(task_id, "failed")
        return

    print(f"[MC Bridge] Executing task {task_id} → url={url} browsers={browsers}")
    await update_task(task_id, "running")
    try:
        from tools.word_report import generate_word_report
        result    = await core_agent.run_multi_browser_with_spec(url, spec, browsers)
        b_list    = result.get("browsers") or browsers
        browser   = b_list[0] if len(b_list) == 1 else "multi"
        word_path = generate_word_report(result, browser=browser)
        result["word_report"] = word_path
        print(f"[MC Bridge] Word report → {word_path}")
        await update_task(task_id, "completed", result)
        print(f"[MC Bridge] Task {task_id} completed.")
    except Exception as exc:
        print(f"[MC Bridge] Task {task_id} failed: {exc}")
        await update_task(task_id, "failed")


# ── 7. Main worker ────────────────────────────────────────────────────────────

async def run_worker(core_agent, poll_interval: int = 20) -> None:
    """Login, connect, then listen via SSE + heartbeat fallback."""
    if not await login():
        print("[MC Bridge] Worker aborted — login failed.")
        return

    if not await connect():
        print("[MC Bridge] Worker aborted — could not connect to Mission Control.")
        return

    async def on_sse_task(task: dict):
        await _execute_task(task, core_agent)

    asyncio.create_task(_listen_sse(on_sse_task))

    print(f"[MC Bridge] Worker started — polling every {poll_interval}s.")
    try:
        while True:
            tasks = await poll_assigned_tasks()
            for task in tasks:
                asyncio.create_task(_execute_task(task, core_agent))
            await asyncio.sleep(poll_interval)
    except asyncio.CancelledError:
        pass
    finally:
        await disconnect()
