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


def test_duplicate_title_alias_across_sections_is_skipped_not_rejected(tmp_path: Path) -> None:
    repo = PrepRepository(f"sqlite:///{tmp_path / 'prep.sqlite'}")
    sections = parse_sections_from_pages([(1, ["I. Operations", "alpha"]), (2, ["II. Operations", "beta"])])

    document_id = repo.save_document(
        path=Path("fixture.pdf"),
        title="Fixture",
        page_count=2,
        content_hash="abc",
        sections=sections,
    )

    assert repo.find_section_by_token("1", document_id=document_id).canonical_id == 1
    assert repo.find_section_by_token("2", document_id=document_id).canonical_id == 2
    with pytest.raises(ValueError, match="Unknown section identifier"):
        repo.find_section_by_token("Operations", document_id=document_id)


def test_repeated_numeric_bullets_do_not_override_chapter_sections() -> None:
    pages = [
        (1, ["Chapter 1", "Introduction", "1. First project goal", "2. Second project goal"]),
        (2, ["1. First limitation", "2. Second limitation", "Chapter 2 reviews the next topic."]),
        (3, ["Chapter 2", "Method", "1. First method step", "2. Second method step"]),
    ]

    parsed = parse_sections_from_pages(pages)

    assert [(section.source_label, section.title) for section in parsed] == [
        ("Chapter 1", "Introduction"),
        ("Chapter 2", "Method"),
    ]


def test_unreliable_repeated_section_references_fall_back_to_page_ranges() -> None:
    pages = [
        (page, [f"Section 1.4 reference on page {page}", "ordinary paragraph text"])
        for page in range(1, 61)
    ]

    parsed = parse_sections_from_pages(pages)

    assert len(parsed) > 1
    assert parsed[0].source_label.startswith("Pages ")
    assert all(section.title.startswith("Document Pages ") for section in parsed)
