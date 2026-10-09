"""FastAPI application for the single-user local SOME workbench."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from . import actions
from .planner import HELP_MESSAGE, route_message
from .receipts import (
    acknowledge_recovery,
    finish_receipt,
    get_receipt,
    list_receipts,
    recover_interrupted_receipts,
    receipt_facts,
    receipt_summary,
    start_receipt,
)
from .security import get_workspace, safe_filename, safe_path

@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # SOME currently runs one local worker. Interrupted operations are never
    # replayed automatically because their side effects may already have occurred.
    await run_in_threadpool(recover_interrupted_receipts)
    yield


app = FastAPI(
    lifespan=lifespan,
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
STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


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
    """Apply common response headers, basic local request limits, and origin checks."""
    response = None
    path = request.url.path

    if path == "/api/upload" and request.method == "POST":
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                request_length = int(raw_length)
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid request length."},
                )
            else:
                # Multipart framing adds overhead beyond the 10 MiB file limit.
                if request_length > actions.MAX_UPLOAD_BYTES + 1024 * 1024:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "Uploads are limited to 10 MiB."},
                    )

    if response is None and path.startswith("/api/"):
        origin = request.headers.get("origin")
        if origin and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            expected_origin = f"{request.url.scheme}://{request.url.netloc}"
            if origin.rstrip("/") != expected_origin.rstrip("/"):
                response = JSONResponse(
                    status_code=403,
                    content={"detail": "Cross-origin state changes are not allowed."},
                )

        if response is None:
            now = time.monotonic()
            client_host = request.client.host if request.client else "local"
            bucket = _rate_buckets[client_host]
            while bucket and bucket[0] < now - 60:
                bucket.popleft()
            if len(bucket) >= MAX_REQUESTS_PER_MINUTE:
                response = JSONResponse(
                    status_code=429,
                    content={"detail": "Local request limit reached. Try again shortly."},
                )
            else:
                bucket.append(now)

            if len(_rate_buckets) > 500:
                for key in list(_rate_buckets):
                    entries = _rate_buckets[key]
                    while entries and entries[0] < now - 60:
                        entries.popleft()
                    if not entries:
                        _rate_buckets.pop(key, None)

    if response is None:
        response = await call_next(request)

    # Apply headers to ordinary responses and early security rejections alike.
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; img-src 'self' data:; base-uri 'none'; "
        "frame-ancestors 'none'; form-action 'self'"
    )
    if path.startswith("/api/"):
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
async def upload_file(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    root = get_workspace()
    try:
        filename = safe_filename(file.filename or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    upload_dir = safe_path("uploads", root)
    upload_dir.mkdir(parents=True, exist_ok=True)
    receipt_id = await run_in_threadpool(start_receipt, "upload_file")
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
        await run_in_threadpool(
            finish_receipt, receipt_id, "failed", "Upload rejected; no completed file was recorded."
        )
        raise
    except OSError as exc:
        target.unlink(missing_ok=True)
        await run_in_threadpool(
            finish_receipt, receipt_id, "failed", "Upload failed; inspect the workspace before retrying."
        )
        raise HTTPException(status_code=500, detail="The file could not be stored locally.") from exc
    finally:
        await file.close()

    await run_in_threadpool(
        finish_receipt,
        receipt_id,
        "succeeded",
        f"Uploaded file data ({size} bytes); file was not executed.",
        {"size_bytes": size},
    )
    return {
        "ok": True,
        "message": f"Saved {filename}. The file was stored as data and was not executed.",
        "file": f"uploads/{stored_name}",
        "size_bytes": size,
        "receipt_id": receipt_id,
    }


@app.post("/api/chat")
async def chat(body: ChatRequest) -> dict[str, Any]:
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Enter a message.")
    root = get_workspace()
    receipt_id: str | None = None
    action_name: str | None = None
    try:
        csv_files = await run_in_threadpool(_files_for_planner, root)
        intent = await run_in_threadpool(route_message, message, csv_files)
        name = intent["action"]
        action_name = name
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

        # Persist the running record before invoking any workspace operation.
        receipt_id = await run_in_threadpool(start_receipt, name)

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
                    "receipt_id": receipt_id,
                }
            await run_in_threadpool(
                finish_receipt,
                receipt_id,
                "succeeded",
                receipt_summary(name, result),
                receipt_facts(name, result),
            )
            return {
                "kind": "plan",
                "message": (
                    f"Preview ready: {result['move_count']} file(s) would move. "
                    "Nothing has changed yet."
                ),
                "plan_id": plan_id,
                "receipt_id": receipt_id,
                "result": result,
            }
        elif name == "profile_csv":
            result = await run_in_threadpool(actions.profile_csv, args["file"], root)
        elif name == "clean_csv":
            result = await run_in_threadpool(actions.clean_csv, args["file"], root)
        elif name == "create_note":
            result = await run_in_threadpool(actions.create_note, args["content"], root)
        else:
            await run_in_threadpool(
                finish_receipt, receipt_id, "failed", "Rejected unknown action."
            )
            return {"kind": "help", "message": HELP_MESSAGE}

        await run_in_threadpool(
            finish_receipt,
            receipt_id,
            "succeeded",
            receipt_summary(name, result),
            receipt_facts(name, result),
        )
        return {
            "kind": "result",
            "message": _result_message(result),
            "result": result,
            "receipt_id": receipt_id,
        }
    except (ValueError, FileNotFoundError) as exc:
        if receipt_id:
            await run_in_threadpool(
                finish_receipt,
                receipt_id,
                "failed",
                f"{action_name or 'Action'} failed; inspect the workspace before retrying.",
            )
        return {"kind": "error", "message": str(exc), "receipt_id": receipt_id}
    except OSError:
        if receipt_id:
            await run_in_threadpool(
                finish_receipt,
                receipt_id,
                "failed",
                f"{action_name or 'Action'} failed; inspect the workspace before retrying.",
            )
        return {
            "kind": "error",
            "message": "The local operation failed. Check workspace permissions and available disk space.",
            "receipt_id": receipt_id,
        }


@app.get("/api/receipts")
async def action_receipts(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100_000),
) -> dict[str, Any]:
    """List durable receipts without exposing file paths or content."""
    items = await run_in_threadpool(list_receipts, limit, offset)
    return {"items": items, "count": len(items), "limit": limit, "offset": offset}


@app.get("/api/receipts/{receipt_id}")
async def action_receipt(receipt_id: str) -> dict[str, Any]:
    item = await run_in_threadpool(get_receipt, receipt_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    return item


@app.post("/api/receipts/{receipt_id}/acknowledge-recovery")
async def acknowledge_interrupted_action(receipt_id: str) -> dict[str, Any]:
    item = await run_in_threadpool(get_receipt, receipt_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Receipt not found.")
    if item["status"] != "interrupted":
        raise HTTPException(
            status_code=409,
            detail="Only interrupted operations need recovery acknowledgement.",
        )
    acknowledged = await run_in_threadpool(acknowledge_recovery, receipt_id)
    if acknowledged is None:
        raise HTTPException(status_code=409, detail="Receipt state changed; refresh and try again.")
    return acknowledged


@app.get("/api/artifacts/{artifact_path:path}", include_in_schema=False)
async def download_artifact(artifact_path: str) -> FileResponse:
    """Download a generated or uploaded workspace file without exposing arbitrary paths."""
    try:
        target = safe_path(artifact_path, get_workspace(), must_exist=True)
    except (ValueError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail="That workspace artifact was not found.") from exc
    if not target.is_file():
        raise HTTPException(status_code=404, detail="That workspace artifact was not found.")
    return FileResponse(
        target,
        filename=target.name,
        media_type="application/octet-stream",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.post("/api/plans/{plan_id}/confirm")
async def confirm_plan(plan_id: str) -> dict[str, Any]:
    async with app.state.plan_lock:
        _clean_expired_plans()
        plan = app.state.pending_plans.get(plan_id)
        if plan is None:
            raise HTTPException(status_code=404, detail="This plan expired or no longer exists.")

        receipt_id = await run_in_threadpool(start_receipt, "apply_organization")
        root = get_workspace()
        try:
            result = await run_in_threadpool(
                actions.apply_organization, plan["moves"], root
            )
            verified_moves = 0
            for move in result["moves"]:
                source = safe_path(move["source"], root)
                target = safe_path(move["target"], root, must_exist=True)
                if not source.exists() and target.is_file():
                    verified_moves += 1
            result["verified_moves"] = verified_moves
            app.state.pending_plans.pop(plan_id, None)

            if verified_moves != result["moved_count"]:
                summary = (
                    "Organization ran but post-action verification was inconclusive; "
                    "inspect the workspace before retrying."
                )
                await run_in_threadpool(
                    finish_receipt,
                    receipt_id,
                    "interrupted",
                    summary,
                    receipt_facts("apply_organization", result),
                )
                return {
                    "kind": "error",
                    "message": summary,
                    "receipt_id": receipt_id,
                    "result": result,
                }

            await run_in_threadpool(
                finish_receipt,
                receipt_id,
                "succeeded",
                receipt_summary("apply_organization", result),
                receipt_facts("apply_organization", result),
            )
        except (OSError, ValueError) as exc:
            app.state.pending_plans.pop(plan_id, None)
            await run_in_threadpool(
                finish_receipt,
                receipt_id,
                "failed",
                "File organization failed; inspect the workspace before retrying.",
            )
            raise HTTPException(
                status_code=409,
                detail=(
                    "The plan could not be applied safely. Inspect the workspace before retrying. "
                    f"Receipt: {receipt_id}"
                ),
            ) from exc
    return {
        "kind": "result",
        "message": _result_message(result),
        "result": result,
        "receipt_id": receipt_id,
    }


@app.delete("/api/plans/{plan_id}")
async def cancel_plan(plan_id: str) -> dict[str, str]:
    async with app.state.plan_lock:
        existed = app.state.pending_plans.pop(plan_id, None) is not None
    if not existed:
        raise HTTPException(status_code=404, detail="This plan expired or no longer exists.")
    return {"status": "cancelled", "message": "No files were moved."}
