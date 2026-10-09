"""Durable, privacy-minimizing action receipts for the local SOME runtime.

Receipts store operation names, state, timestamps, count-based verification facts,
and safe summaries. They deliberately do not persist raw prompts, note bodies,
CSV cell values, or file paths.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .security import get_workspace

_FINAL_STATES = {"succeeded", "failed", "cancelled", "interrupted"}
_INIT_LOCK = threading.Lock()
_INITIALIZED: set[str] = set()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _database_path() -> Path:
    configured = os.environ.get("SOME_RECEIPTS_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    # Keep the SQLite file adjacent to, not inside, the workspace so ordinary
    # workspace listings and file-organization plans never touch the database.
    return get_workspace().parent / "receipts.sqlite3"


def _ensure_initialized() -> Path:
    path = _database_path()
    key = str(path)
    with _INIT_LOCK:
        if key not in _INITIALIZED:
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=5)
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA busy_timeout=5000")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS action_receipts (
                        id TEXT PRIMARY KEY,
                        created_at TEXT NOT NULL,
                        finished_at TEXT,
                        action TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (
                            status IN ('running', 'succeeded', 'failed',
                                       'cancelled', 'interrupted')
                        ),
                        summary TEXT NOT NULL,
                        facts_json TEXT NOT NULL DEFAULT '{}',
                        recovery_acknowledged_at TEXT
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_action_receipts_created "
                    "ON action_receipts(created_at DESC)"
                )
                connection.commit()
            finally:
                connection.close()
            _INITIALIZED.add(key)
    return path


def _connect() -> sqlite3.Connection:
    path = _ensure_initialized()
    connection = sqlite3.connect(path, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


@contextlib.contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    connection = _connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _as_public_dict(row: sqlite3.Row) -> dict[str, Any]:
    # Don't return stored JSON verbatim: it may be malformed if the file was
    # manually edited or corrupted. Public receipts only expose count-based facts.
    try:
        facts = json.loads(row["facts_json"])
        if not isinstance(facts, dict):
            facts = {}
    except (TypeError, ValueError):
        facts = {}
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "action": row["action"],
        "status": row["status"],
        "summary": row["summary"],
        "facts": facts,
        "recovery_acknowledged_at": row["recovery_acknowledged_at"],
    }


def receipt_facts(action: str, result: dict[str, Any]) -> dict[str, int | bool]:
    """Select only numeric verification facts; never persist paths or file contents."""
    if action == "list_files":
        return {
            "count": int(result.get("count", 0)),
            "truncated": bool(result.get("truncated", False)),
        }
    if action == "find_duplicates":
        return {
            "group_count": int(result.get("group_count", 0)),
            "files_scanned": int(result.get("files_scanned", 0)),
        }
    if action == "profile_csv":
        missing = result.get("missing_by_column", {})
        return {
            "row_count": int(result.get("row_count", 0)),
            "column_count": len(result.get("columns", [])),
            "duplicate_rows": int(result.get("duplicate_rows", 0)),
            "missing_cells": sum(int(value) for value in missing.values()),
        }
    if action == "clean_csv":
        return {
            "rows_before": int(result.get("rows_before", 0)),
            "rows_after": int(result.get("rows_after", 0)),
            "duplicate_rows_removed": int(result.get("duplicate_rows_removed", 0)),
            "formula_like_cells_neutralized": int(
                result.get("formula_like_cells_neutralized", 0)
            ),
        }
    if action == "create_note":
        return {"characters_saved": int(result.get("characters_saved", 0))}
    if action == "upload_file":
        return {"size_bytes": int(result.get("size_bytes", 0))}
    if action == "preview_organization":
        return {"move_count": int(result.get("move_count", 0))}
    if action == "apply_organization":
        return {
            "moved_count": int(result.get("moved_count", 0)),
            "verified_moves": int(result.get("verified_moves", 0)),
        }
    return {}


def receipt_summary(action: str, result: dict[str, Any]) -> str:
    """Build a short summary without persisting personal file names or note text."""
    if action == "list_files":
        return f"Listed {int(result.get('count', 0))} file(s)."
    if action == "find_duplicates":
        return (
            f"Scanned {int(result.get('files_scanned', 0))} file(s); "
            f"found {int(result.get('group_count', 0))} duplicate group(s)."
        )
    if action == "profile_csv":
        return (
            f"Profiled CSV: {int(result.get('row_count', 0))} row(s), "
            f"{len(result.get('columns', []))} column(s)."
        )
    if action == "clean_csv":
        return (
            f"Cleaned CSV output created; removed "
            f"{int(result.get('duplicate_rows_removed', 0))} duplicate row(s)."
        )
    if action == "create_note":
        return f"Saved note ({int(result.get('characters_saved', 0))} characters)."
    if action == "upload_file":
        return f"Uploaded file data ({int(result.get('size_bytes', 0))} bytes); file was not executed."
    if action == "preview_organization":
        return f"Created a preview for {int(result.get('move_count', 0))} file move(s). No files moved."
    if action == "apply_organization":
        return (
            f"Organization moved {int(result.get('moved_count', 0))} file(s); "
            f"verified {int(result.get('verified_moves', 0))} destination(s)."
        )
    return "Operation completed."


def start_receipt(action: str, summary: str | None = None) -> str:
    """Persist a running receipt before the operation begins."""
    receipt_id = uuid.uuid4().hex
    with _connection() as connection:
        connection.execute(
            """
            INSERT INTO action_receipts
                (id, created_at, action, status, summary, facts_json)
            VALUES (?, ?, ?, 'running', ?, '{}')
            """,
            (receipt_id, _now(), action, summary or "Operation started."),
        )
    return receipt_id


def finish_receipt(
    receipt_id: str,
    status: str,
    summary: str,
    facts: dict[str, int | bool] | None = None,
) -> dict[str, Any]:
    """Close a running receipt exactly once with sanitized verification facts."""
    if status not in _FINAL_STATES:
        raise ValueError(f"Unsupported final receipt status: {status}")
    encoded_facts = json.dumps(facts or {}, sort_keys=True, separators=(",", ":"))
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE action_receipts
            SET status = ?, finished_at = ?, summary = ?, facts_json = ?
            WHERE id = ? AND status = 'running'
            """,
            (status, _now(), summary[:500], encoded_facts, receipt_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Receipt does not exist or is no longer running.")
        row = connection.execute(
            "SELECT * FROM action_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
    return _as_public_dict(row)


def get_receipt(receipt_id: str) -> dict[str, Any] | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT * FROM action_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
    return _as_public_dict(row) if row else None


def list_receipts(limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
    if not 1 <= limit <= 100:
        raise ValueError("Receipt limit must be between 1 and 100.")
    if not 0 <= offset <= 100_000:
        raise ValueError("Receipt offset is out of range.")
    with _connection() as connection:
        rows = connection.execute(
            """
            SELECT * FROM action_receipts
            ORDER BY created_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
    return [_as_public_dict(row) for row in rows]


def recover_interrupted_receipts() -> int:
    """Mark unfinished work as interrupted after a single-worker app restart.

    This records an uncertain outcome; it never retries a possibly non-idempotent
    operation automatically. The user must inspect the workspace before retrying.
    """
    now = _now()
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE action_receipts
            SET status = 'interrupted',
                finished_at = ?,
                summary = 'Process restarted during this operation; inspect the workspace before retrying.'
            WHERE status = 'running'
            """,
            (now,),
        )
    return cursor.rowcount


def acknowledge_recovery(receipt_id: str) -> dict[str, Any] | None:
    """Record that a user reviewed an interrupted operation's recovery warning."""
    with _connection() as connection:
        cursor = connection.execute(
            """
            UPDATE action_receipts
            SET recovery_acknowledged_at = COALESCE(recovery_acknowledged_at, ?)
            WHERE id = ? AND status = 'interrupted'
            """,
            (_now(), receipt_id),
        )
        if cursor.rowcount == 0:
            row = connection.execute(
                "SELECT * FROM action_receipts WHERE id = ?", (receipt_id,)
            ).fetchone()
            if row is None or row["status"] != "interrupted":
                return None
        row = connection.execute(
            "SELECT * FROM action_receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
    return _as_public_dict(row)
