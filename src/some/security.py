"""Path and filename safety helpers for the local workspace."""

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

DEFAULT_WORKSPACE = Path.home() / ".some" / "workspace"


def get_workspace() -> Path:
    """Return a created, resolved workspace root."""
    configured = os.environ.get("SOME_WORKSPACE")
    root = Path(configured).expanduser() if configured else DEFAULT_WORKSPACE
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve(strict=True)


def safe_path(relative: str, root: Path | None = None, *, must_exist: bool = False) -> Path:
    """Resolve a workspace-relative path while rejecting traversal and symlink segments."""
    base = (root or get_workspace()).resolve(strict=True)
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise ValueError("A non-empty relative path is required.")
    if "\\" in relative:
        raise ValueError("Use forward slashes in workspace-relative paths.")

    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or any(part in ("", ".", "..") for part in parsed.parts):
        raise ValueError("The path must stay inside the workspace.")

    current = base
    for part in parsed.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("Symbolic links are not allowed in workspace paths.")

    resolved = current.resolve(strict=must_exist)
    if not resolved.is_relative_to(base) or resolved == base:
        raise ValueError("The path must stay inside the workspace.")
    if must_exist and not resolved.exists():
        raise FileNotFoundError(relative)
    return resolved


def safe_filename(raw: str) -> str:
    """Keep only a safe basename for an uploaded file; never trust client path components."""
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ValueError("A filename is required.")
    basename = raw.replace("\\", "/").split("/")[-1].strip()
    if basename in ("", ".", ".."):
        raise ValueError("The filename is invalid.")
    basename = "".join(ch for ch in basename if ch.isalnum() or ch in " ._-()")
    basename = re.sub(r"\s+", " ", basename).strip(" .")
    if not basename:
        raise ValueError("The filename is invalid.")
    if len(basename) > 120:
        stem, suffix = Path(basename).stem, Path(basename).suffix[:16]
        basename = stem[: max(1, 120 - len(suffix))] + suffix
    if basename in ("", ".", ".."):
        raise ValueError("The filename is invalid.")
    return basename
