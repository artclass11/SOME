"""FastAPI application for the single-user local SOME workbench."""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import actions
from .planner import HELP_MESSAGE, route_message
from .security import get_workspace, safe_filename, safe_path

app = FastAPI(
    title="SOME",
    description="Local-first work through an action-oriented chat interface.",
    version="0.1.0",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.state.pending_plans = {}
app.state.plan_lock = asyncio.Lock()
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)
MAX_REQUESTS_PER_MINUTE = 120
PLAN_TTL_SECONDS = 10 * 60
INDEX_FILE = Path(__file__).parent / "static" / "index.html"


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _clean_expired_plans() -> None:
    cutoff = time.time() - PLAN_TTL_SECONDS
    expired = [
        key for key, plan in app.state.pending_plans.items()
        if plan["created_at"] < cutoff
    ]
    for key in expired:
        app.state.pending_plans.pop(key, None)
    # Bound process memory if a local user repeatedly requests previews.
    while len(app.state.pending_plans) > 50:
        oldest = min(
            app.state.pending_plans,
            key=lambda key: app.state.pending_plans[key]["created_at"],
        )
        app.state.pending_plans.pop(oldest, None)


def _files_for_planner(root: Path) -> list[str]:
    return [
        item["path"] for item in actions.list_files(root)["files"]
        if item["path"].casefold().endswith(".csv")
    ]


def _result_message(result: dict[str, Any]) -> str:
    action = result.get("action")
    if action == "list_files":
        return f"Found {result['count']} file(s) in your workspace."
    if action == "find_duplicates":
        return f"Found {result['group_count']} duplicate group(s) across {result['files_scanned']} file(s)."
    if action == "profile_csv":
        return (
            f"Inspected {result['file']}: {result['row_count']} row(s), "
            f"{len(result['columns'])} column(s), "
            f"{result['duplicate_rows']} duplicate row(s)."
        )
    if action == "clean_csv":
        return (
            f"Cleaned {result['source']} and saved {result['output']}. "
            f"Removed {result['duplicate_rows_removed']} duplicate row(s); "
            "the original file is unchanged."
        )
    if action == "create_note":
        return f"Saved your note to {result['file']}."
    if action == "apply_organization":
        return f"Moved {result['moved_count']} file(s) using the approved plan."
    return "The action completed."


@app.middleware("http")
async def secure_local_api(request, call_next):
    """Apply basic hardening and per-process request limits; not remote auth."""
    if request.url.path == "/api/upload" and request.method == "POST":
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                request_length = int(raw_length)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Invalid request length."})
            # Multipart framing adds a small overhead around the actual 10 MiB file limit.
            if request_length > actions.MAX_UPLOAD_BYTES + 1024 * 1024:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Uploads are limited to 10 MiB."},
                )
    if request.url.path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin.rstrip("/") != expected_origin.rstrip("/"):
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Cross-origin state changes are not allowed."},
                )
        now = time.monotonic()
        client_host = request.client.host if request.client else "local"
        bucket = _rate_buckets[client_host]
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= MAX_REQUESTS_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={"detail": "Local request limit reached. Try again shortly."},
            )
        bucket.append(now)
        if len(_rate_buckets) > 500:
            for key in list(_rate_buckets):
                entries = _rate_buckets[key]
                while entries and entries[0] < now - 60:
                    entries.popleft()
                if not entries:
                    _rate_buckets.pop(key, None)

    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    if not INDEX_FILE.is_file():
        raise HTTPException(status_code=500, detail="The SOME interface is missing.")
    return FileResponse(INDEX_FILE, media_type="text/html")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "runtime": "local-only",
        "planner": "ollama-optional" if os.environ.get("SOME_OLLAMA_MODEL") else "rules",
    }


@app.get("/api/files")
async def workspace_files() -> dict[str, Any]:
    try:
        root = get_workspace()
        return await run_in_threadpool(actions.list_files, root)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Could not read the local workspace.") from exc


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    root = get_workspace()
    try:
        filename = safe_filename(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload_dir = safe_path("uploads", root)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex[:12]}-{filename}"
    target = safe_path(f"uploads/{stored_name}", root)
    size = 0
    try:
        with target.open("xb") as stream:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > actions.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Uploads are limited to 10 MiB.")
                stream.write(chunk)
    except HTTPException:
        target.unlink(missing_ok=True)
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="The file could not be stored locally.") from exc
    finally:
        await file.close()

    return {
        "ok": True,
        "message": f"Saved {filename}. The file was stored as data and was not executed.",
        "file": f"uploads/{stored_name}",
        "size_bytes": size,
    }


@app.post("/api/chat")
async def chat(body: ChatRequest) -> dict[str, Any]:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Enter a message.")
    root = get_workspace()
    try:
        csv_files = await run_in_threadpool(_files_for_planner, root)
        intent = await run_in_threadpool(route_message, message, csv_files)
        name = intent["action"]
        args = intent.get("args", {})

        if name in ("unknown", "help"):
            return {
                "kind": "help",
                "message": intent.get("message", HELP_MESSAGE),
            }

        if name == "choose_csv":
            error = args.get("file_error")
            if error == "none":
                prompt = "No CSV file is available. Upload a CSV, then ask me to inspect or clean it."
            elif error == "ambiguous":
                options = "\n".join(f"• {item}" for item in args.get("candidates", []))
                prompt = f"Which CSV should I use? Mention its filename:\n{options}"
            else:
                prompt = "I couldn't find that CSV inside the workspace. Upload it or check the filename."
            return {"kind": "help", "message": prompt}

        if name == "list_files":
            result = await run_in_threadpool(actions.list_files, root)
        elif name == "find_duplicates":
            result = await run_in_threadpool(actions.find_duplicates, root)
        elif name == "preview_organization":
            result = await run_in_threadpool(actions.preview_organization, root)
            plan_id = uuid.uuid4().hex
            async with app.state.plan_lock:
                _clean_expired_plans()
                app.state.pending_plans[plan_id] = {
                    "created_at": time.time(),
                    "moves": result["moves"],
                }
            return {
                "kind": "plan",
                "message": (
                    f"Preview ready: {result['move_count']} file(s) would move. "
                    "Nothing has changed yet."
                ),
                "plan_id": plan_id,
                "result": result,
            }
        elif name == "profile_csv":
            result = await run_in_threadpool(actions.profile_csv, args["file"], root)
        elif name == "clean_csv":
            result = await run_in_threadpool(actions.clean_csv, args["file"], root)
        elif name == "create_note":
            result = await run_in_threadpool(actions.create_note, args["content"], root)
        else:
            # Defense in depth: unknown model output never becomes a tool invocation.
            return {"kind": "help", "message": HELP_MESSAGE}

        return {"kind": "result", "message": _result_message(result), "result": result}
    except (ValueError, FileNotFoundError) as exc:
        return {"kind": "error", "message": str(exc)}
    except OSError:
        return {
            "kind": "error",
            "message": "The local operation failed. Check workspace permissions and available disk space.",
        }


@app.post("/api/plans/{plan_id}/confirm")
async def confirm_plan(plan_id: str) -> dict[str, Any]:
    async with app.state.plan_lock:
        _clean_expired_plans()
        plan = app.state.pending_plans.get(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="This plan expired or no longer exists.")
        try:
            result = await run_in_threadpool(actions.apply_organization, plan["moves"], get_workspace())
        except (OSError, ValueError) as exc:
            raise HTTPException(
                status_code=409,
                detail="The plan could not be applied safely. Nothing was intentionally overwritten.",
            ) from exc
        app.state.pending_plans.pop(plan_id, None)
    return {"kind": "result", "message": _result_message(result), "result": result}


@app.delete("/api/plans/{plan_id}")
async def cancel_plan(plan_id: str) -> dict[str, str]:
    async with app.state.plan_lock:
        existed = app.state.pending_plans.pop(plan_id, None) is not None
    if not existed:
        raise HTTPException(status_code=404, detail="This plan expired or no longer exists.")
    return {"status": "cancelled", "message": "No files were moved."}
