import sys
import asyncio

if sys.platform == "win32" and sys.version_info < (3, 12):
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import os
import json
import glob
import logging
import builtins
from datetime import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse

from agent.core_agent import CoreAgent
from tools.llm import extract_spec_text
from tools.trello import is_configured as trello_configured
from tools.word_report import generate_word_report

app = FastAPI(title="OMNISHORE QA Agent")

os.makedirs("output", exist_ok=True)
app.mount("/static", StaticFiles(directory="frontend"), name="static")


# ── Live log streaming ────────────────────────────────────────────────────────

_log_subs: list[asyncio.Queue] = []
_orig_print = builtins.print


def _broadcast_print(*args, sep=" ", end="\n", file=None, flush=False):
    _orig_print(*args, sep=sep, end=end, file=file, flush=flush)
    if file is None:
        line = sep.join(str(a) for a in args).strip()
        if line:
            for q in list(_log_subs):
                try:
                    q.put_nowait(line)
                except Exception:
                    pass


def _broadcast_event(data: dict):
    """Broadcast a structured JSON event to all SSE subscribers."""
    payload = json.dumps(data, ensure_ascii=False)
    for q in list(_log_subs):
        try:
            q.put_nowait(payload)
        except Exception:
            pass


def _broadcast_done():
    for q in list(_log_subs):
        try:
            q.put_nowait("__DONE__")
        except Exception:
            pass


builtins.print = _broadcast_print


class _SSELogHandler(logging.Handler):
    """Forward Python logging records to the SSE broadcast queue."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            for q in list(_log_subs):
                try:
                    q.put_nowait(line)
                except Exception:
                    pass
        except Exception:
            pass


_sse_handler = _SSELogHandler()
_sse_handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))

# Attach to the root logger so all modules' loggers propagate here
_root_logger = logging.getLogger()
_root_logger.addHandler(_sse_handler)
_root_logger.setLevel(logging.INFO)


@app.get("/api/logs")
async def stream_logs():
    queue: asyncio.Queue = asyncio.Queue(maxsize=300)
    _log_subs.append(queue)

    async def generate():
        try:
            while True:
                try:
                    line = await asyncio.wait_for(queue.get(), timeout=120.0)
                    if line == "__DONE__":
                        yield "data: __DONE__\n\n"
                        return
                    safe = line.replace("\n", " ").replace("\r", "")
                    yield f"data: {safe}\n\n"
                except asyncio.TimeoutError:
                    yield "data: ping\n\n"
        finally:
            try:
                _log_subs.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── UI ────────────────────────────────────────────────────────────────────────

@app.get("/")
async def serve_ui():
    return FileResponse("frontend/index.html")


# ── Runs history ──────────────────────────────────────────────────────────────

@app.get("/api/runs")
async def list_runs():
    files = sorted(glob.glob("output/report_*.json"), reverse=True)
    runs  = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            runs.append({
                "file":         os.path.basename(path),
                "timestamp":    data.get("timestamp"),
                "url":          data.get("url", ""),
                "total_tests":  data.get("total_tests", 0),
                "passed":       data.get("passed", 0),
                "failed":       data.get("failed", 0),
                "partial":      data.get("partial", 0),
                "plan_only":    data.get("plan_only", False),
                "multi_browser": data.get("multi_browser", False),
            })
        except Exception:
            continue
    return runs


@app.get("/api/runs/{filename}")
async def get_run(filename: str):
    if not filename.startswith("report_") or not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = os.path.join("output", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Run not found.")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── Agent run endpoints ───────────────────────────────────────────────────────

def _normalize_url(url: str) -> str:
    url = url.strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


@app.post("/api/run-agent")
async def run_agent(
    url: str = Form(...),
    browsers: str = Form(default="chromium"),
):
    url = _normalize_url(url)
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
    browser_list = [b.strip() for b in browsers.split(",") if b.strip()] or ["chromium"]
    agent = CoreAgent()
    try:
        return await agent.run_multi_browser(url, browser_list, emit_fn=_broadcast_event)
    finally:
        _broadcast_done()


@app.post("/api/run-agent-with-spec")
async def run_agent_with_spec(
    url: str = Form(default=""),
    browsers: str = Form(default="chromium"),
    spec_file: UploadFile = File(default=None),
):
    has_file = spec_file is not None and spec_file.filename
    has_url  = bool(url and url.strip())
    if not has_file and not has_url:
        raise HTTPException(status_code=400, detail="Provide a spec file and/or a target URL.")

    spec_text = ""
    if has_file:
        allowed = (".md", ".txt", ".pdf", ".docx", ".doc")
        if not spec_file.filename.lower().endswith(allowed):
            raise HTTPException(status_code=400, detail=f"Unsupported file type. Allowed: {', '.join(allowed)}")
        content = await spec_file.read()
        if not content:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")
        spec_text = extract_spec_text(content, spec_file.filename)
        print(f"[SPEC] Loaded '{spec_file.filename}' — {len(spec_text)} chars")

    browser_list = [b.strip() for b in browsers.split(",") if b.strip()] or ["chromium"]
    agent = CoreAgent()
    try:
        return await agent.run_multi_browser_with_spec(
            _normalize_url(url), spec_text, browser_list, emit_fn=_broadcast_event
        )
    finally:
        _broadcast_done()


# ── Word export ───────────────────────────────────────────────────────────────

@app.get("/api/runs/{filename}/export/word")
async def export_word(filename: str):
    if not filename.startswith("report_") or not filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    path = os.path.join("output", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Run not found.")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    browsers = data.get("browsers") or [data.get("browser", "chromium")]
    browser  = browsers[0] if len(browsers) == 1 else "multi"
    word_path = generate_word_report(data, browser=browser)
    return FileResponse(
        word_path,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"rapport_omnishore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
    )


# ── Generate report only (direct .docx download) ─────────────────────────────

@app.post("/api/generate-report-only")
async def generate_report_only(
    url: str = Form(default=""),
    browsers: str = Form(default="chromium"),
    spec_file: UploadFile = File(default=None),
):
    """Run full pipeline and return the Word report directly as a download."""
    has_file = spec_file is not None and spec_file.filename
    has_url  = bool(url and url.strip())
    if not has_file and not has_url:
        raise HTTPException(status_code=400, detail="Provide a spec file and/or a target URL.")

    browser_list = [b.strip() for b in browsers.split(",") if b.strip()] or ["chromium"]
    agent = CoreAgent()
    try:
        if has_file:
            content   = await spec_file.read()
            spec_text = extract_spec_text(content, spec_file.filename)
            print(f"[SPEC] Loaded '{spec_file.filename}' — {len(spec_text)} chars")
            data = await agent.run_multi_browser_with_spec(
                _normalize_url(url), spec_text, browser_list, emit_fn=_broadcast_event
            )
        else:
            data = await agent.run_multi_browser(
                _normalize_url(url), browser_list, emit_fn=_broadcast_event
            )

        browser   = browser_list[0] if len(browser_list) == 1 else "multi"
        word_path = generate_word_report(data, browser=browser)
        return FileResponse(
            word_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"rapport_omnishore_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx",
        )
    finally:
        _broadcast_done()


# ── Trello ────────────────────────────────────────────────────────────────────

@app.get("/api/trello/status")
async def trello_status():
    return {"connected": trello_configured()}


@app.post("/api/trello/test")
async def trello_test():
    if not trello_configured():
        return {
            "ok": False,
            "error": "Trello not configured — check TRELLO_API_KEY, TRELLO_TOKEN, TRELLO_LIST_ID in .env",
        }
    from tools.trello import create_failure_card
    result = create_failure_card(
        test_id="TEST_CONNECTION",
        error_details="Test card created by OMNISHORE QA Agent to verify Trello integration.",
        screenshot_path=None,
        description="Trello connection test",
    )
    if result.get("status") == "created":
        return {"ok": True, "card_url": result.get("url"), "message": "Card created successfully."}
    return {"ok": False, "error": result.get("error", "Unknown error")}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
