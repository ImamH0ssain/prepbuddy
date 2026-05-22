"""Path helpers for managed uploads and Windows/WSL display paths."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath


def safe_filename(filename: str) -> str:
    """Return a lowercase PDF filename safe for local storage."""
    name = PureWindowsPath(filename).name
    name = PurePosixPath(name).name
    stem = Path(name).stem or "document"
    suffix = Path(name).suffix.lower() or ".pdf"
    safe_stem = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return f"{safe_stem or 'document'}{suffix}"


def managed_upload_path(upload_dir: Path, filename: str, content: bytes) -> Path:
    """Build a deterministic managed-upload path from content hash and filename."""
    prefix = hashlib.sha256(content).hexdigest()[:8]
    return upload_dir / f"{prefix}_{safe_filename(filename)}"


def windows_display_path(path: str | Path) -> str:
    """Return a best-effort Windows display path for a stored path."""
    text = str(path)
    if text.startswith("/mnt/") and len(text) > 6 and text[5].isalpha() and text[6] == "/":
        drive = text[5].upper()
        rest = text[7:].replace("/", "\\")
        return f"{drive}:\\{rest}"
    return text.replace("/", "\\")


def wsl_display_path(path: str | Path) -> str:
    """Return a best-effort WSL display path for a stored path."""
    text = str(path).replace("\\", "/")
    if len(text) >= 3 and text[1] == ":" and text[2] == "/":
        drive = text[0].lower()
        return f"/mnt/{drive}/{text[3:]}"
    return text

