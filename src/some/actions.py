"""Typed, bounded actions. No action executes uploaded code."""

from __future__ import annotations

import csv
import hashlib
import os
import re
import shutil
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .security import get_workspace, safe_filename, safe_path

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_CSV_ROWS = 100_000
MAX_DUPLICATE_FILES = 5_000
MAX_FILE_HASH_BYTES = 100 * 1024 * 1024
MAX_TOTAL_HASH_BYTES = 512 * 1024 * 1024
MAX_NOTE_CHARS = 5_000

EXTENSION_CATEGORY = {
    ".jpg": "images", ".jpeg": "images", ".png": "images", ".gif": "images",
    ".webp": "images", ".heic": "images", ".svg": "images",
    ".pdf": "documents", ".doc": "documents", ".docx": "documents",
    ".txt": "documents", ".md": "documents", ".rtf": "documents",
    ".csv": "spreadsheets", ".xls": "spreadsheets", ".xlsx": "spreadsheets",
    ".ods": "spreadsheets",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio", ".flac": "audio",
    ".mp4": "video", ".mov": "video", ".mkv": "video", ".webm": "video",
    ".zip": "archives", ".7z": "archives", ".rar": "archives", ".tar": "archives",
    ".py": "code", ".js": "code", ".ts": "code", ".html": "code", ".css": "code",
    ".json": "data", ".xml": "data", ".yaml": "data", ".yml": "data",
}


def _walk_regular_files(root: Path) -> list[Path]:
    """Walk without following symlinked directories or returning symlinked files."""
    found: list[Path] = []
    for folder, directories, filenames in os.walk(root, followlinks=False):
        current = Path(folder)
        directories[:] = [
            name for name in directories
            if not (current / name).is_symlink()
        ]
        for name in filenames:
            candidate = current / name
            if candidate.is_symlink() or not candidate.is_file():
                continue
            try:
                candidate.resolve(strict=True).relative_to(root)
            except (OSError, ValueError):
                continue
            found.append(candidate)
    return found


def list_files(root: Path | None = None) -> dict[str, Any]:
    base = (root or get_workspace()).resolve(strict=True)
    files = _walk_regular_files(base)
    files.sort(key=lambda item: item.relative_to(base).as_posix().casefold())
    return {
        "action": "list_files",
        "count": len(files),
        "files": [
            {
                "path": item.relative_to(base).as_posix(),
                "size_bytes": item.stat().st_size,
            }
            for item in files[:500]
        ],
        "truncated": len(files) > 500,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_duplicates(root: Path | None = None) -> dict[str, Any]:
    base = (root or get_workspace()).resolve(strict=True)
    files = _walk_regular_files(base)
    if len(files) > MAX_DUPLICATE_FILES:
        raise ValueError(f"Duplicate scan is limited to {MAX_DUPLICATE_FILES} files.")
    by_size: dict[int, list[Path]] = defaultdict(list)
    for item in files:
        by_size[item.stat().st_size].append(item)

    groups: list[list[str]] = []
    total_hashed = 0
    for same_size in by_size.values():
        if len(same_size) < 2:
            continue
        for item in same_size:
            size = item.stat().st_size
            if size > MAX_FILE_HASH_BYTES:
                continue
            total_hashed += size
            if total_hashed > MAX_TOTAL_HASH_BYTES:
                raise ValueError("Duplicate scan reached its total byte budget; use a smaller workspace.")
        by_hash: dict[str, list[Path]] = defaultdict(list)
        for item in same_size:
            if item.stat().st_size <= MAX_FILE_HASH_BYTES:
                by_hash[_sha256(item)].append(item)
        for duplicates in by_hash.values():
            if len(duplicates) > 1:
                groups.append(sorted(p.relative_to(base).as_posix() for p in duplicates))

    return {
        "action": "find_duplicates",
        "group_count": len(groups),
        "duplicates": groups,
        "files_scanned": len(files),
        "hash_byte_budget": MAX_TOTAL_HASH_BYTES,
    }


def _resolve_csv(name: str, root: Path) -> Path:
    candidate = safe_path(name, root, must_exist=True)
    if not candidate.is_file() or candidate.suffix.casefold() != ".csv":
        raise ValueError("Choose an existing CSV file inside the workspace.")
    if candidate.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("CSV files must be 10 MiB or smaller.")
    return candidate


def _read_csv(path: Path) -> tuple[list[str], list[list[str]]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream)
            try:
                raw_headers = next(reader)
            except StopIteration:
                raise ValueError("The CSV file is empty.") from None
            if not raw_headers:
                raise ValueError("The CSV needs a header row.")
            headers: list[str] = []
            used: set[str] = set()
            for index, raw in enumerate(raw_headers, 1):
                normalized = re.sub(r"[^a-zA-Z0-9]+", "_", raw.strip().lower()).strip("_")
                normalized = normalized or f"column_{index}"
                base = normalized
                duplicate = 2
                while normalized in used:
                    normalized = f"{base}_{duplicate}"
                    duplicate += 1
                used.add(normalized)
                headers.append(normalized)
            rows: list[list[str]] = []
            for row in reader:
                if len(rows) >= MAX_CSV_ROWS:
                    raise ValueError(f"CSV row limit of {MAX_CSV_ROWS} reached.")
                if not row:
                    continue
                values = [cell.strip() for cell in row]
                if len(values) < len(headers):
                    values.extend([""] * (len(headers) - len(values)))
                elif len(values) > len(headers):
                    values = values[: len(headers) - 1] + [",".join(values[len(headers) - 1 :])]
                rows.append(values)
            return headers, rows
    except UnicodeDecodeError as exc:
        raise ValueError("This CSV must use UTF-8 or UTF-8 with BOM encoding.") from exc
    except csv.Error as exc:
        raise ValueError("The CSV could not be parsed.") from exc


def profile_csv(filename: str, root: Path | None = None) -> dict[str, Any]:
    base = (root or get_workspace()).resolve(strict=True)
    path = _resolve_csv(filename, base)
    headers, rows = _read_csv(path)
    missing = {
        header: sum(1 for row in rows if not row[index].strip())
        for index, header in enumerate(headers)
    }
    return {
        "action": "profile_csv",
        "file": path.relative_to(base).as_posix(),
        "columns": headers,
        "row_count": len(rows),
        "missing_by_column": missing,
        "duplicate_rows": len(rows) - len({tuple(row) for row in rows}),
    }


def _safe_csv_cell(value: str) -> str:
    """Reduce spreadsheet formula injection risk in generated CSV artifacts."""
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def clean_csv(filename: str, root: Path | None = None) -> dict[str, Any]:
    base = (root or get_workspace()).resolve(strict=True)
    source = _resolve_csv(filename, base)
    headers, rows = _read_csv(source)
    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)

    output_dir = safe_path("outputs", base)
    output_dir.mkdir(parents=True, exist_ok=True)
    target_name = f"{source.stem}.cleaned.csv"
    target = safe_path(f"outputs/{target_name}", base)
    number = 2
    while target.exists():
        target_name = f"{source.stem}.cleaned-{number}.csv"
        target = safe_path(f"outputs/{target_name}", base)
        number += 1

    safe_headers = [_safe_csv_cell(header) for header in headers]
    formula_cells = sum(
        1 for row in unique for value in row
        if value.lstrip().startswith(("=", "+", "-", "@"))
    )
    temp = safe_path(f"outputs/.{uuid.uuid4().hex}.tmp", base)
    try:
        with temp.open("x", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(safe_headers)
            for row in unique:
                writer.writerow([_safe_csv_cell(value) for value in row])
        # Publish with a no-clobber hard link: another file created at the target
        # between preview and publish is never silently overwritten.
        while True:
            try:
                os.link(temp, target)
                break
            except FileExistsError:
                target_name = f"{source.stem}.cleaned-{number}.csv"
                number += 1
                target = safe_path(f"outputs/{target_name}", base)
        temp.unlink(missing_ok=True)
    finally:
        if temp.exists():
            temp.unlink(missing_ok=True)

    return {
        "action": "clean_csv",
        "source": source.relative_to(base).as_posix(),
        "output": target.relative_to(base).as_posix(),
        "rows_before": len(rows),
        "rows_after": len(unique),
        "duplicate_rows_removed": len(rows) - len(unique),
        "formula_like_cells_neutralized": formula_cells,
        "columns": headers,
    }


def create_note(content: str, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Add note text after 'create note:'.")
    if len(content) > MAX_NOTE_CHARS:
        raise ValueError(f"Notes are limited to {MAX_NOTE_CHARS} characters.")
    base = (root or get_workspace()).resolve(strict=True)
    notes_dir = safe_path("notes", base)
    notes_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target_name = safe_filename(f"{stamp}-{uuid.uuid4().hex[:8]}.txt")
    target = safe_path(f"notes/{target_name}", base)
    with target.open("x", encoding="utf-8", newline="") as stream:
        stream.write(content.strip() + "\n")
    return {
        "action": "create_note",
        "file": target.relative_to(base).as_posix(),
        "characters_saved": len(content.strip()),
    }


def preview_organization(root: Path | None = None) -> dict[str, Any]:
    base = (root or get_workspace()).resolve(strict=True)
    candidates = [
        p for p in _walk_regular_files(base)
        if p.parent == base and p.name != ".DS_Store"
    ]
    candidates.sort(key=lambda item: item.name.casefold())
    moves: list[dict[str, str]] = []
    reserved: set[str] = set()
    for source in candidates:
        category = EXTENSION_CATEGORY.get(source.suffix.casefold(), "other")
        initial = f"organized/{category}/{source.name}"
        target_name = initial
        index = 2
        while target_name.casefold() in reserved or safe_path(target_name, base).exists():
            stem, suffix = source.stem, source.suffix
            target_name = f"organized/{category}/{stem}-{index}{suffix}"
            index += 1
        reserved.add(target_name.casefold())
        moves.append({"source": source.relative_to(base).as_posix(), "target": target_name})
    return {"action": "preview_organization", "move_count": len(moves), "moves": moves}


def apply_organization(moves: list[dict[str, str]], root: Path | None = None) -> dict[str, Any]:
    base = (root or get_workspace()).resolve(strict=True)
    validated: list[tuple[Path, Path, str, str]] = []
    targets: set[str] = set()
    for move in moves:
        source_name, target_name = move.get("source"), move.get("target")
        source = safe_path(source_name, base, must_exist=True)
        target = safe_path(target_name, base)
        if source.parent != base or not source.is_file():
            raise ValueError("Only top-level regular files can be organized.")
        if target.exists() or target.as_posix().casefold() in targets:
            raise ValueError("A destination already exists; create a fresh preview.")
        if not target.is_relative_to(base) or target == base:
            raise ValueError("A destination must stay inside the workspace.")
        targets.add(target.as_posix().casefold())
        validated.append((source, target, source_name, target_name))

    completed: list[tuple[Path, Path]] = []
    try:
        for source, target, _, _ in validated:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Re-resolve all path segments after creating directories and before the move.
            source = safe_path(source_name, base, must_exist=True)
            target = safe_path(target_name, base)
            if source.parent != base or source.is_symlink() or target.exists():
                raise ValueError("A path changed during organization; no overwrite was allowed.")
            shutil.move(str(source), str(target))
            completed.append((source, target))
    except Exception:
        for source, target in reversed(completed):
            try:
                source.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and not source.exists():
                    shutil.move(str(target), str(source))
            except OSError:
                # The original exception remains the actionable failure; manual recovery may be needed.
                pass
        raise

    return {
        "action": "apply_organization",
        "moved_count": len(completed),
        "moves": [
            {"source": source_name, "target": target_name}
            for _, _, source_name, target_name in validated
        ],
    }
