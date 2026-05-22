"""Helpers for safe project `.env` updates."""

from __future__ import annotations

from pathlib import Path


def update_gemini_key(env_path: Path, api_key: str) -> None:
    """Upsert GEMINI_API_KEY in `.env` while preserving unrelated lines."""
    key = api_key.strip()
    if not key:
        raise ValueError("Gemini API key cannot be empty")
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    updated = False
    output: list[str] = []
    for line in lines:
        if line.startswith("GEMINI_API_KEY="):
            output.append(f"GEMINI_API_KEY={key}")
            updated = True
        else:
            output.append(line)
    if not updated:
        output.append(f"GEMINI_API_KEY={key}")
    env_path.write_text("\n".join(output) + "\n", encoding="utf-8")


def gemini_key_status(*, settings_key: str | None, session_key: str | None) -> str:
    """Return a non-secret status label for Gemini key availability."""
    if session_key:
        return "set for this session"
    if settings_key:
        return "loaded from environment/.env"
    return "not set"

