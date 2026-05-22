"""Section alias normalization and scenario-to-source section resolution."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .schemas import Section

if TYPE_CHECKING:
    from .repository import PrepRepository


def normalize_alias(value: str | int) -> str:
    """Normalize section labels and titles into stable lookup aliases."""
    text = str(value).strip().lower()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def load_mapping_override(path: Path) -> dict[str, str]:
    """Load an optional reviewer-facing scenario mapping file."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Section mapping file must contain a JSON object: {path}")
    return {str(key): str(value) for key, value in raw.items()}


class SectionResolver:
    """Resolve user-provided section IDs, labels, or aliases to stored sections."""

    def __init__(
        self,
        repository: "PrepRepository",
        *,
        document_id: int | None = None,
        mapping_override: dict[str, Any] | None = None,
    ) -> None:
        self.repository = repository
        self.document_id = document_id
        self.mapping_override = {str(key): str(value) for key, value in (mapping_override or {}).items()}

    def resolve(self, token: str | int) -> Section:
        """Resolve one section token, applying explicit scenario overrides first."""
        requested = str(token).strip()
        target = self.mapping_override.get(requested, requested)
        return self.repository.find_section_by_token(target, document_id=self.document_id)

    def resolve_many(self, tokens: list[str | int]) -> list[Section]:
        """Resolve a list of requested section identifiers while preserving order."""
        seen: set[int] = set()
        resolved: list[Section] = []
        for token in tokens:
            section = self.resolve(token)
            if section.id not in seen:
                resolved.append(section)
                seen.add(section.id)
        return resolved

