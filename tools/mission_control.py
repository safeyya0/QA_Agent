"""
Mission Control bridge for OMNISHORE QA Agent.

Connection flow:
  1. POST /api/connect  → obtain connection_id, agent_id, sse_url, heartbeat_url
  2. Listen to SSE /api/events for real-time task assignments
  3. Poll GET /api/agents/{id}/heartbeat as fallback (work_items → assigned_tasks)
  4. PUT /api/tasks/{id} to report results back

Environment variables:
  MISSION_CONTROL_URL      — e.g. http://localhost:3000  (required to enable)
  MISSION_CONTROL_API_KEY  — x-api-key header value      (required)
"""
import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MC_URL     = (os.getenv("MISSION_CONTROL_URL") or "").rstrip("/")
MC_API_KEY = os.getenv("MISSION_CONTROL_API_KEY", "")

# ── Shared state set once after POST /api/connect ─────────────────────────────
_connection_id: str | None = None
_agent_id:      int | None = None
_heartbeat_url: str | None = None
_sse_url:       str | None = None

_processed: set[int] = set()   # avoid running the same task twice


def _headers() -> dict:
    return {"Content-Type": "application/json", "x-api-key": MC_API_KEY}


# ── 1. Bridge connection ───────────────────────────────────────────────────────

async def connect() -> bool:
    """Register OMNISHORE as a direct CLI connection. Populates module-level state."""
    global _connection_id, _agent_id, _heartbeat_url, _sse_url

    payload = {
        "tool_name":    "omnishore-qa",
        "agent_name":   "omnishore-qa",
        "agent_role":   "QA agent — automated browser testing on web applications",
        "tool_version": "1.0.0",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{MC_URL}/api/connect", headers=_headers(), json=payload)
            resp.raise_for_status()
            data = resp.json()

        _connection_id = data.get("connection_id")
        _agent_id      = data.get("agent_id")
        _heartbeat_url = f"{MC_URL}{data.get('heartbeat_url', f'/api/agents/{_agent_id}/heartbeat')}"
        _sse_url       = f"{MC_URL}{data.get('sse_url', '/api/events')}"

        logger.info(
            "[MC Bridge] Connected — connection_id=%s  agent_id=%s",
            _connection_id, _agent_id,
        )
        return True
    except Exception as exc:
        logger.warning("[MC Bridge] connect() failed: %s", exc)
        return False


async def disconnect() -> None:
    """Gracefully deregister the bridge connection."""
    if not _connection_id:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.delete(
                f"{MC_URL}/api/connect",
                headers=_headers(),
                params={"connection_id": _connection_id},
            )
        logger.info("[MC Bridge] Disconnected (connection_id=%s)", _connection_id)
    except Exception as exc:
        logger.debug("[MC Bridge] disconnect() error: %s", exc)


# ── 2. Fetch assigned tasks via heartbeat ──────────────────────────────────────

async def poll_assigned_tasks() -> list[dict]:
    """
    GET /api/agents/{id}/heartbeat

    Returns the list of task objects under work_items where type == 'assigned_tasks'.
    """
    if not _heartbeat_url:
        return []
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(_heartbeat_url, headers=_headers())
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


# ── 3. Update task status ──────────────────────────────────────────────────────

async def update_task(task_id: int, status: str, result: dict | None = None) -> bool:
    """
    PUT /api/tasks/{id}

    status values accepted by Mission Control:
      running | completed | failed | cancelled
    """
    payload: dict = {"status": status}
    if result is not None:
        payload["result"] = (
            f"PASSED:{result.get('passed',0)}  "
            f"FAILED:{result.get('failed',0)}  "
            f"PARTIAL:{result.get('partial',0)}  "
            f"/ {result.get('total_tests',0)} tests  "
            f"[{', '.join(result.get('browsers', []))}]"
        )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{MC_URL}/api/tasks/{task_id}",
                headers=_headers(),
                json=payload,
            )
            resp.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("[MC Bridge] update_task(%s) error: %s", task_id, exc)
        return False


# ── 4. SSE listener ───────────────────────────────────────────────────────────

async def _listen_sse(on_task) -> None:
    """
    Stream /api/events and call on_task(task_dict) whenever an
    'assigned_tasks' event arrives for our agent.
    """
    if not _sse_url:
        return

    logger.info("[MC Bridge] SSE listener connecting to %s", _sse_url)
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("GET", _sse_url, headers={**_headers(), "Accept": "text/event-stream"}) as resp:
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

                    # Filter: task assigned to our agent
                    if (
                        event.get("type") in ("task_assigned", "assigned_tasks", "task_updated")
                        and event.get("agent_id") == _agent_id
                    ):
                        task = event.get("data") or event.get("task") or event
                        await on_task(task)
    except Exception as exc:
        logger.warning("[MC Bridge] SSE stream error: %s", exc)


# ── 5. Task execution helper ───────────────────────────────────────────────────

def _parse_task_params(task: dict) -> tuple[str, str, list[str]]:
    """
    Extract (url, spec, browsers) from a Mission Control task object.

    Convention: put a JSON string in the task description field:
        {"url": "https://example.com", "spec": "Test login...", "browsers": "chromium,firefox"}
    Or just a plain URL string.
    """
    raw = task.get("description") or task.get("title") or task.get("name") or ""
    try:
        params = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        params = {"url": raw.strip()}

    url      = params.get("url", "")
    spec     = params.get("spec", "")
    browsers = [b.strip() for b in params.get("browsers", "chromium").split(",") if b.strip()]
    return url, spec, browsers


async def _execute_task(task: dict, core_agent) -> None:
    task_id = task.get("id")
    if not task_id or task_id in _processed:
        return
    _processed.add(task_id)

    url, spec, browsers = _parse_task_params(task)
    if not url:
        logger.warning("[MC Bridge] Task %s has no URL — marking failed.", task_id)
        await update_task(task_id, "failed")
        return

    logger.info("[MC Bridge] Executing task %s → url=%s browsers=%s", task_id, url, browsers)
    await update_task(task_id, "running")
    try:
        result = await core_agent.run_multi_browser_with_spec(url, spec, browsers)
        await update_task(task_id, "completed", result)
        logger.info("[MC Bridge] Task %s completed.", task_id)
    except Exception as exc:
        logger.error("[MC Bridge] Task %s failed: %s", task_id, exc)
        await update_task(task_id, "failed")


# ── 6. Main worker ────────────────────────────────────────────────────────────

async def run_worker(core_agent, poll_interval: int = 20) -> None:
    """
    Entry point called from main.py on startup.

    Strategy:
      • POST /api/connect  → get connection_id + agent_id
      • Launch SSE listener in background for real-time events
      • Poll heartbeat every `poll_interval` seconds as a reliable fallback
    """
    ok = await connect()
    if not ok:
        logger.error("[MC Bridge] Worker aborted — could not connect to Mission Control.")
        return

    async def on_sse_task(task: dict):
        await _execute_task(task, core_agent)

    # SSE listener runs concurrently; if it drops it will error-log and exit silently
    asyncio.create_task(_listen_sse(on_sse_task))

    logger.info("[MC Bridge] Polling heartbeat every %ss as fallback.", poll_interval)
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
