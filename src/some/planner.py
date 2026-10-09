"""Turn chat messages into a restricted action request, never executable code."""

from __future__ import annotations

import json
import os
import re
from pathlib import PurePosixPath
from urllib.error import URLError
from urllib.request import Request, urlopen
from typing import Any

ALLOWED_ACTIONS = {
    "list_files",
    "find_duplicates",
    "preview_organization",
    "clean_csv",
    "profile_csv",
    "create_note",
}

HELP_MESSAGE = (
    "Tell SOME what to do: show my files, find duplicates, inspect a CSV, "
    "clean a CSV, organize files, or create note: followed by your own text."
)


def _note_content(message: str) -> str | None:
    match = re.match(
        r"^\s*(?:please\s+)?(?:create|make|save|write)\s+(?:a\s+)?note\s*:\s*(.*)$",
        message,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1).strip() if match else None


def _choose_csv(message: str, csv_files: list[str]) -> dict[str, Any]:
    """Choose only from existing CSV files returned by the filesystem scan."""
    mentioned = [
        path for path in csv_files
        if PurePosixPath(path).name.casefold() in message.casefold()
        or path.casefold() in message.casefold()
    ]
    if len(mentioned) == 1:
        return {"file": mentioned[0]}

    explicit = re.findall(r"[\w().-]+\.csv", message, flags=re.IGNORECASE)
    if explicit:
        token = explicit[-1].casefold()
        matches = [
            path for path in csv_files
            if path.casefold() == token or PurePosixPath(path).name.casefold() == token
        ]
        if len(matches) == 1:
            return {"file": matches[0]}
        # Uploads receive a unique prefix to avoid overwrites; accept the original basename.
        prefixed = [
            path for path in csv_files
            if PurePosixPath(path).name.casefold().endswith("-" + token)
        ]
        if len(prefixed) == 1:
            return {"file": prefixed[0]}
        return {"file_error": "not_found"}

    if len(csv_files) == 1:
        return {"file": csv_files[0]}
    if not csv_files:
        return {"file_error": "none"}
    return {"file_error": "ambiguous", "candidates": sorted(csv_files, key=str.casefold)}


def _deterministic_route(message: str, csv_files: list[str]) -> dict[str, Any]:
    cleaned = message.strip()
    lower = cleaned.casefold()

    note = _note_content(cleaned)
    if note is not None:
        if not note:
            return {"action": "help", "args": {}, "message": "Add the note text after the colon."}
        return {"action": "create_note", "args": {"content": note}}

    if any(term in lower for term in (
        "find duplicate", "duplicates", "duplicate files", "same files", "repeated files",
    )):
        return {"action": "find_duplicates", "args": {}}

    if any(term in lower for term in (
        "organize files", "organise files", "sort my files", "sort files",
        "organize my downloads", "organise my downloads", "organize my files",
        "organise my files",
    )):
        return {"action": "preview_organization", "args": {}}

    if any(term in lower for term in (
        "show files", "list files", "my files", "what files", "workspace files",
    )):
        return {"action": "list_files", "args": {}}

    csv_context = ".csv" in lower or any(
        term in lower for term in ("csv", "spreadsheet", "sheet data", "table data")
    )
    if csv_context and any(term in lower for term in (
        "clean", "tidy", "deduplicate", "remove duplicate rows", "format",
    )):
        choice = _choose_csv(cleaned, csv_files)
        if "file_error" in choice:
            return {"action": "choose_csv", "args": choice}
        return {"action": "clean_csv", "args": {"file": choice["file"]}}

    if csv_context and any(term in lower for term in (
        "profile", "inspect", "analyze", "analyse", "summarize", "summary",
        "missing values", "columns", "rows",
    )):
        choice = _choose_csv(cleaned, csv_files)
        if "file_error" in choice:
            return {"action": "choose_csv", "args": choice}
        return {"action": "profile_csv", "args": {"file": choice["file"]}}

    if lower in {"help", "start", "what can you do", "commands"}:
        return {"action": "help", "args": {}, "message": HELP_MESSAGE}

    return {"action": "unknown", "args": {}, "message": HELP_MESSAGE}


def _local_model_route(message: str, csv_files: list[str]) -> dict[str, Any] | None:
    """Ask an optional loopback Ollama server for intent JSON, then validate it."""
    model = os.environ.get("SOME_OLLAMA_MODEL", "").strip()
    if not model:
        return None

    system_prompt = (
        "Classify the user's request as one action from this exact allowlist: "
        "list_files, find_duplicates, preview_organization, clean_csv, profile_csv, create_note, unknown. "
        "Return JSON with keys action and file only. file must be the exact relative path of an existing "
        "CSV from this list, or null: " + json.dumps(csv_files, ensure_ascii=False) + ". "
        "Never propose shell commands, code, URLs, or new tools. Prefer unknown when uncertain. "
        "A note may only be created when the user's message explicitly starts a note command."
    )
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": message[:4000]},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0},
    }).encode("utf-8")
    request = Request(
        "http://127.0.0.1:11434/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=2.0) as response:
            envelope = json.loads(response.read(64 * 1024).decode("utf-8"))
        content = envelope.get("message", {}).get("content", "")
        payload = json.loads(content)
    except (OSError, URLError, ValueError, TypeError, KeyError):
        return None

    action = payload.get("action") if isinstance(payload, dict) else None
    if action not in ALLOWED_ACTIONS:
        return None

    # Note content is always sourced directly from the user's own explicit command.
    if action == "create_note":
        content_from_user = _note_content(message)
        if content_from_user:
            return {"action": "create_note", "args": {"content": content_from_user}}
        return None

    if action in ("clean_csv", "profile_csv"):
        file = payload.get("file")
        if not isinstance(file, str) or file not in csv_files:
            chosen = _choose_csv(message, csv_files)
            if "file" not in chosen:
                return {"action": "choose_csv", "args": chosen}
            file = chosen["file"]
        return {"action": action, "args": {"file": file}}

    return {"action": action, "args": {}}


def route_message(message: str, csv_files: list[str]) -> dict[str, Any]:
    """Resolve to one known action; the tool runtime still validates all arguments."""
    # Explicit, deterministic commands take precedence over inference.
    explicit = _deterministic_route(message, csv_files)
    if explicit["action"] != "unknown":
        return explicit

    inferred = _local_model_route(message, csv_files)
    if inferred is not None:
        return inferred
    return explicit
