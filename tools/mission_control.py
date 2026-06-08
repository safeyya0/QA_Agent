# Bridge between the QA agent and Mission Control.
# Logs in, connects, then listens for tasks via SSE + heartbeat polling.
# Set MISSION_CONTROL_URL in .env to enable — without it nothing runs.
import asyncio
import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

MC_URL  = (os.getenv("MISSION_CONTROL_URL") or "").rstrip("/")
MC_USER = os.getenv("MISSION_CONTROL_USER", "admin")
MC_PASS = os.getenv("MISSION_CONTROL_PASS", "")

# kept at module level so login/connect/worker all share the same session
_session_cookie: str | None = None
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
            cookie = resp.headers.get("set-cookie", "")
            for part in cookie.split(","):
                part = part.strip()
                if "mc_session" in part or "session" in part.lower():
                    _session_cookie = part.split(";")[0].strip()
                    break
            if not _session_cookie:
                # just grab whatever cookie is there
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


async def connect() -> bool:
    """Register this agent with MC and grab the connection/heartbeat URLs."""
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


async def poll_assigned_tasks() -> list[dict]:
    """Check heartbeat for new tasks + pick up any that OpenClaw failed to dispatch."""
    tasks = []

    if _heartbeat_url:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(_heartbeat_url, headers=_auth_headers())
                resp.raise_for_status()
                data = resp.json()
            for bucket in (data.get("work_items") or []):
                if bucket.get("type") == "assigned_tasks":
                    tasks.extend(bucket.get("items") or [])
        except Exception as exc:
            logger.debug("[MC Bridge] heartbeat error: %s", exc)

    # also grab failed tasks in case OpenClaw dropped them
    if _agent_id:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{MC_URL}/api/tasks",
                    headers=_auth_headers(),
                    params={"assignee": _agent_id, "status": "failed"},
                )
                resp.raise_for_status()
                data = resp.json()
            failed = data if isinstance(data, list) else (data.get("tasks") or [])
            for t in failed:
                # Only reclaim if failed by dispatch (not by us)
                err = (t.get("result") or t.get("error") or "").lower()
                if "openclaw" in err or "dispatch" in err or "enoent" in err or not err:
                    if t not in tasks:
                        tasks.append(t)
        except Exception as exc:
            logger.debug("[MC Bridge] failed-tasks query error: %s", exc)

    return tasks


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
        print(f"[MC Bridge] update_task({task_id}) error: {exc}")
        return False


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


# ping OmniBot directly so it doesn't have to wait for the SSE to fire

_OMNIBOT_URL = (os.getenv("OMNIBOT_URL") or "").rstrip("/")


async def _notify_omnibot(
    task_id: int, task: dict, result: dict, word_path: str
) -> None:
    if not _OMNIBOT_URL:
        return
    passed  = result.get("passed", 0)
    failed  = result.get("failed", 0)
    partial = result.get("partial", 0)
    total   = result.get("total_tests", 0)
    payload = {
        "event": "activity.task_status_changed",
        "data": {
            "id":               task_id,
            "title":            task.get("title", f"Task {task_id}"),
            "status":           "quality_review",
            "assigned_to":      "omnishore-qa",
            "result":           f"PASSED:{passed}  FAILED:{failed}  PARTIAL:{partial}  / {total} tests",
            "word_report_path": os.path.abspath(word_path) if word_path else "",
        },
    }
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(f"{_OMNIBOT_URL}/webhook", json=payload)
        print(f"[MC Bridge] OmniBot notified for task {task_id}.")
    except Exception as exc:
        logger.debug("[MC Bridge] OmniBot notify error: %s", exc)



def _parse_task_params(task: dict) -> tuple[str, str, list[str]]:
    raw = task.get("description") or task.get("title") or task.get("name") or ""
    try:
        params = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        params = {"url": raw.strip()}
    url  = params.get("url", "")
    spec = params.get("spec", "")
    if spec and not spec.startswith("#") and len(spec) < 260:
        import pathlib
        p = pathlib.Path(spec)
        if p.suffix in (".md", ".txt"):
            if p.exists():
                spec = p.read_text(encoding="utf-8")
                print(f"[MC Bridge] Loaded spec from {p}")
            else:
                print(f"[MC Bridge] Spec file not found: {p}")
                spec = ""
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
    if not spec:
        print(f"[MC Bridge] Task {task_id} has no spec (.md) — marking failed.")
        await update_task(task_id, "failed")
        return

    print(f"[MC Bridge] Executing task {task_id} → url={url} browsers={browsers}")
    await update_task(task_id, "in_progress")
    try:
        from tools.word_report import generate_word_report
        result    = await core_agent.run_multi_browser_with_spec(url, spec, browsers)
        b_list    = result.get("browsers") or browsers
        browser   = b_list[0] if len(b_list) == 1 else "multi"
        word_path = generate_word_report(result, browser=browser)
        result["word_report"] = word_path
            # Find the matching JSON report (report_TIMESTAMP.json) for the download URL
        import glob as _glob
        json_files  = sorted(_glob.glob("output/report_*.json"), reverse=True)
        json_report = os.path.basename(json_files[0]) if json_files else None
        omnishore_url = os.getenv("OMNISHORE_URL", "http://localhost:8002")
        if json_report:
            download_url = f"{omnishore_url}/api/runs/{json_report}/export/word"
            result["word_report"] = download_url
            print(f"[MC Bridge] Word report → {download_url}")
        else:
            result["word_report"] = word_path
        ok = await update_task(task_id, "quality_review", result)
        if not ok:
            logger.warning("[MC Bridge] update_task(%s) to quality_review failed — task may be stuck.", task_id)
        print(f"[MC Bridge] Task {task_id} completed.")
        await _save_report_to_memory(task_id, url, result)
        await _notify_omnibot(task_id, task, result, word_path)
    except Exception as exc:
        print(f"[MC Bridge] Task {task_id} failed: {exc}")
        await update_task(task_id, "failed")


async def _save_report_to_memory(task_id: int, url: str, result: dict) -> None:
    """Save a markdown report summary to Mission Control Memory → Files."""
    from datetime import datetime
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M")
    word_url = result.get("word_report", "N/A")
    content  = f"""# Rapport QA — Task {task_id}

**Date :** {ts}
**URL testée :** {url}

## Résultats
| Statut | Nombre |
|--------|--------|
| PASSED | {result.get('passed', 0)} |
| FAILED | {result.get('failed', 0)} |
| PARTIAL | {result.get('partial', 0)} |
| **Total** | **{result.get('total_tests', 0)}** |

## Télécharger le rapport Word

[Cliquer ici pour télécharger le rapport Word]({word_url})
"""
    payload = {
        "action":   "create",
        "path":     f"reports/task_{task_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md",
        "content":  content,
        "agent":    "omnishore-qa",
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{MC_URL}/api/memory",
                headers=_auth_headers(),
                json=payload,
            )
            resp.raise_for_status()
        print(f"[MC Bridge] Report saved to Memory/Files → {payload['path']}")
    except Exception as exc:
        logger.debug("[MC Bridge] memory save error: %s", exc)


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
