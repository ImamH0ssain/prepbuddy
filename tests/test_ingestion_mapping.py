from __future__ import annotations

from pathlib import Path

import pytest

from prepbuddy.ingestion import parse_sections_from_pages
from prepbuddy.mapping import SectionResolver, normalize_alias
from prepbuddy.repository import PrepRepository


def test_parse_sections_from_section_numbered_dossier() -> None:
    pages = [
        (1, ["Table of Contents", "1. Identity", "Section 1. Identity, Background, and Public Status", "one"]),
        (2, ["still one", "Section 2. Powers, Abilities, and Documented Limits", "two"]),
    ]

    parsed = parse_sections_from_pages(pages)

    assert [section.canonical_id for section in parsed] == [1, 2]
    assert parsed[0].source_label == "Section 1"
    assert parsed[0].title == "Identity, Background, and Public Status"
    assert "still one" in parsed[0].text


def test_parse_sections_assigns_canonical_ids_for_roman_headings() -> None:
    pages = [
        (1, ["I. Operations", "alpha"]),
        (2, ["II. Case Studies", "beta"]),
        (3, ["III. Appendix", "gamma"]),
    ]

    parsed = parse_sections_from_pages(pages)

    assert [(section.canonical_id, section.source_label, section.title) for section in parsed] == [
        (1, "I", "Operations"),
        (2, "II", "Case Studies"),
        (3, "III", "Appendix"),
    ]


def test_parse_sections_falls_back_to_title_like_headings() -> None:
    pages = [
        (1, ["Operations", "alpha"]),
        (2, ["Known Bases", "beta"]),
        (3, ["Case Files", "gamma"]),
    ]

    parsed = parse_sections_from_pages(pages)

    assert [section.source_label for section in parsed] == ["Operations", "Known Bases", "Case Files"]
    assert [section.canonical_id for section in parsed] == [1, 2, 3]


def test_section_resolver_uses_explicit_mapping_override(tmp_path: Path) -> None:
    repo = PrepRepository(f"sqlite:///{tmp_path / 'prep.sqlite'}")
    document_id = repo.save_document(
        path=Path("fixture.pdf"),
        title="Fixture",
        page_count=3,
        content_hash="abc",
        sections=parse_sections_from_pages([(1, ["I. Operations", "alpha"]), (2, ["II. Case Studies", "beta"])]),
    )
    resolver = SectionResolver(repo, document_id=document_id, mapping_override={"5": "Case Studies"})

    resolved = resolver.resolve_many(["5"])

    assert resolved[0].canonical_id == 2
    assert resolved[0].title == "Case Studies"


def test_normalize_alias_handles_labels_and_titles() -> None:
    assert normalize_alias("Section 8") == "section-8"
    assert normalize_alias("Known Bases, Safehouses, and Operational Territory") == (
        "known-bases-safehouses-and-operational-territory"
    )


def test_duplicate_alias_across_sections_is_rejected(tmp_path: Path) -> None:
    repo = PrepRepository(f"sqlite:///{tmp_path / 'prep.sqlite'}")
    sections = parse_sections_from_pages([(1, ["I. Operations", "alpha"]), (2, ["II. Operations", "beta"])])

    with pytest.raises(ValueError, match="Duplicate section alias"):
        repo.save_document(
            path=Path("fixture.pdf"),
            title="Fixture",
            page_count=2,
            content_hash="abc",
            sections=sections,
        )

