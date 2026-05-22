"""PDF ingestion, section detection, and chunking."""

from __future__ import annotations

import hashlib
import math
import re
from pathlib import Path

from .schemas import ParsedChunk, ParsedSection

SECTION_RE = re.compile(r"^Section\s+([A-Za-z0-9][A-Za-z0-9.\-]*)\.?\s*[:\-]?\s*(.+)$", re.IGNORECASE)
CHAPTER_RE = re.compile(r"^Chapter\s+([0-9]+|[IVXLCDM]+)\*?\b\.?\s*(.*)$", re.IGNORECASE)
SPACED_CHAPTER_RE = re.compile(r"^C\s*H\s*A\s*P\s*T\s*E\s*R$", re.IGNORECASE)
NUMERIC_RE = re.compile(r"^([0-9]+)\.\s+(?![0-9])(.+)$")
ROMAN_RE = re.compile(r"^([IVXLCDM]+)[.)]\s+(.+)$", re.IGNORECASE)
SUBSECTION_RE = re.compile(r"^\d+\.\d+\s+")
FOOTER_RE = re.compile(r"^\S+\.(?:md|pdf)\s+\d{4}-\d{2}-\d{2}$|^\d+\s*/\s*\d+$", re.IGNORECASE)


class PDFIngestionError(RuntimeError):
    """Raised when a PDF cannot be parsed into useful sections."""


def file_hash(path: Path) -> str:
    """Return a SHA-256 hash for an input PDF."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def extract_pdf_pages(path: Path) -> tuple[str, int, list[tuple[int, list[str]]]]:
    """Extract sorted text lines from a machine-readable PDF with PyMuPDF."""
    try:
        import fitz
    except ImportError as exc:  # pragma: no cover - environment guard
        raise PDFIngestionError("PyMuPDF is not installed; install pymupdf to ingest PDFs") from exc

    if not path.exists():
        raise PDFIngestionError(f"PDF not found: {path}")
    try:
        document = fitz.open(path)
    except Exception as exc:  # pragma: no cover - PyMuPDF detail varies
        raise PDFIngestionError(f"Could not open PDF {path}: {exc}") from exc

    pages: list[tuple[int, list[str]]] = []
    try:
        title = document.metadata.get("title") or path.stem
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text", sort=True)
            lines = [_clean_line(line) for line in text.splitlines()]
            pages.append((page_index, [line for line in lines if line]))
        return title, document.page_count, pages
    finally:
        document.close()


def parse_pdf(path: Path) -> tuple[str, int, str, list[ParsedSection]]:
    """Parse a PDF into top-level sections and chunks."""
    title, page_count, pages = extract_pdf_pages(path)
    sections = parse_sections_from_pages(pages)
    return title, page_count, file_hash(path), sections


def parse_sections_from_pages(pages: list[tuple[int, list[str]]]) -> list[ParsedSection]:
    """Detect top-level sections from extracted page lines."""
    flattened: list[tuple[int, str]] = [
        (page_number, line)
        for page_number, lines in pages
        for line in lines
        if line and not FOOTER_RE.match(line)
    ]
    if not flattened:
        raise PDFIngestionError("PDF contains no machine-readable text")

    candidates = _find_candidates(flattened)
    if not candidates:
        return _fallback_page_range_sections(flattened)

    sections: list[ParsedSection] = []
    for idx, candidate in enumerate(candidates):
        start = candidate["index"]
        end = candidates[idx + 1]["index"] if idx + 1 < len(candidates) else len(flattened)
        section_lines = flattened[start:end]
        text = "\n".join(line for _, line in section_lines)
        page_start = section_lines[0][0]
        page_end = section_lines[-1][0]
        sections.append(
            _finalize_section(
                canonical_id=idx + 1,
                source_label=candidate["source_label"],
                title=candidate["title"],
                text=text,
                page_start=page_start,
                page_end=page_end,
            )
        )
    return sections


def _find_candidates(flattened: list[tuple[int, str]]) -> list[dict[str, str | int]]:
    section_candidates = _collect_candidates(flattened, mode="section")
    if _is_candidate_set_plausible(section_candidates):
        return section_candidates

    chapter_candidates = _collect_candidates(flattened, mode="chapter")
    if _is_candidate_set_plausible(chapter_candidates):
        return chapter_candidates

    if len(section_candidates) == 1:
        return section_candidates
    if len(chapter_candidates) == 1:
        return chapter_candidates

    numeric_candidates = _collect_candidates(flattened, mode="numeric")
    if _is_candidate_set_plausible(numeric_candidates):
        return numeric_candidates

    roman_candidates = _collect_candidates(flattened, mode="roman")
    if _is_candidate_set_plausible(roman_candidates):
        return roman_candidates

    fallback_candidates = _collect_candidates(flattened, mode="fallback")
    return fallback_candidates if _is_candidate_set_plausible(fallback_candidates) else []


def _collect_candidates(flattened: list[tuple[int, str]], *, mode: str) -> list[dict[str, str | int]]:
    candidates: list[dict[str, str | int]] = []
    for index, (page_number, _line) in enumerate(flattened):
        parsed = _parse_heading(flattened, index, mode=mode)
        if not parsed:
            continue
        source_label, title = parsed
        if _looks_like_toc_noise(title):
            continue
        candidates.append({"index": index, "page": page_number, "source_label": source_label, "title": title})
    return candidates


def _parse_heading(flattened: list[tuple[int, str]], index: int, *, mode: str) -> tuple[str, str] | None:
    line = flattened[index][1]
    if mode == "section":
        match = SECTION_RE.match(line)
        if match:
            return f"Section {match.group(1).rstrip('.')}", _clean_title(match.group(2))
        return None
    if mode == "chapter":
        match = CHAPTER_RE.match(line)
        if match:
            label = f"Chapter {match.group(1).rstrip('.')}"
            inline_title = _clean_title(match.group(2))
            if inline_title and inline_title[0].islower():
                return None
            title = inline_title or _next_title_line(flattened, index) or label
            return label, title
        if SPACED_CHAPTER_RE.match(line):
            return _parse_spaced_chapter(flattened, index)
        return None
    if mode == "numeric":
        match = NUMERIC_RE.match(line)
        if match:
            return match.group(1), _clean_title(match.group(2))
        return None
    if mode == "roman":
        match = ROMAN_RE.match(line)
        if match and _is_roman(match.group(1)):
            return match.group(1).upper(), _clean_title(match.group(2))
        return None
    if mode == "fallback" and _is_title_like(line):
        return _clean_title(line), _clean_title(line)
    return None


def _is_candidate_set_plausible(candidates: list[dict[str, str | int]]) -> bool:
    """Reject heading sets that look like repeated bullets, references, or dense tables."""
    if len(candidates) < 2:
        return False
    labels = [str(candidate["source_label"]).lower() for candidate in candidates]
    if len(set(labels)) != len(labels):
        return False
    page_counts: dict[int, int] = {}
    for candidate in candidates:
        page = int(candidate["page"])
        page_counts[page] = page_counts.get(page, 0) + 1
    if len(candidates) >= 4 and max(page_counts.values()) > max(3, len(candidates) // 3):
        return False
    return True


def _next_title_line(flattened: list[tuple[int, str]], index: int) -> str:
    for _, line in flattened[index + 1 : index + 4]:
        if CHAPTER_RE.match(line) or SPACED_CHAPTER_RE.match(line):
            return ""
        if _is_title_like(line):
            return _clean_title(line)
    return ""


def _parse_spaced_chapter(flattened: list[tuple[int, str]], index: int) -> tuple[str, str] | None:
    title_lines = []
    for _, line in flattened[index + 1 : index + 4]:
        if not line or CHAPTER_RE.match(line) or SPACED_CHAPTER_RE.match(line):
            break
        title_lines.append(line)
        joined = " ".join(title_lines)
        match = re.search(r"\b([0-9]+|[IVXLCDM]+)\s*$", joined, re.IGNORECASE)
        if match:
            label = f"Chapter {match.group(1).upper() if _is_roman(match.group(1)) else match.group(1)}"
            title = _clean_title(joined[: match.start()])
            return label, title or label
    return None


def _fallback_page_range_sections(flattened: list[tuple[int, str]]) -> list[ParsedSection]:
    """Split arbitrary PDFs into stable page-range sections when headings are unreliable."""
    pages = sorted({page_number for page_number, _ in flattened})
    if len(pages) <= 12:
        text = "\n".join(line for _, line in flattened)
        return [_finalize_section(1, "Document", "Document", text, flattened[0][0], flattened[-1][0])]

    group_count = min(40, max(2, math.ceil(len(pages) / 25)))
    group_size = math.ceil(len(pages) / group_count)
    sections: list[ParsedSection] = []
    for canonical_id, start in enumerate(range(0, len(pages), group_size), start=1):
        page_group = set(pages[start : start + group_size])
        lines = [(page, line) for page, line in flattened if page in page_group]
        page_start = min(page_group)
        page_end = max(page_group)
        text = "\n".join(line for _, line in lines)
        label = f"Pages {page_start}-{page_end}"
        sections.append(
            _finalize_section(
                canonical_id=canonical_id,
                source_label=label,
                title=f"Document Pages {page_start}-{page_end}",
                text=text,
                page_start=page_start,
                page_end=page_end,
            )
        )
    return sections


def _finalize_section(
    canonical_id: int,
    source_label: str,
    title: str,
    text: str,
    page_start: int,
    page_end: int,
) -> ParsedSection:
    chunks = _chunk_section(canonical_id, text, page_start, page_end)
    return ParsedSection(
        canonical_id=canonical_id,
        source_label=source_label,
        title=title,
        text=text,
        page_start=page_start,
        page_end=page_end,
        chunks=chunks,
    )


def _chunk_section(canonical_id: int, text: str, page_start: int, page_end: int, max_chars: int = 4500) -> list[ParsedChunk]:
    parts = _split_by_subsection(text)
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(part) > max_chars:
            if current.strip():
                chunks.append(current.strip())
                current = ""
            chunks.extend(_split_long_text(part, max_chars=max_chars))
            continue
        if len(current) + len(part) + 2 > max_chars and current.strip():
            chunks.append(current.strip())
            current = part
        else:
            current = f"{current}\n{part}".strip()
    if current.strip():
        chunks.append(current.strip())
    if not chunks:
        chunks = [text.strip()]
    return [
        ParsedChunk(
            chunk_index=index,
            chunk_id=f"s{canonical_id}:c{index}",
            text=chunk,
            page_start=page_start,
            page_end=page_end,
        )
        for index, chunk in enumerate(chunks, start=1)
    ]


def _split_by_subsection(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if SUBSECTION_RE.match(line) and current:
            parts.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        parts.append("\n".join(current).strip())
    return [part for part in parts if part]


def _split_long_text(text: str, *, max_chars: int) -> list[str]:
    paragraphs = [item.strip() for item in text.split("\n") if item.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if len(current) + len(paragraph) + 1 > max_chars and current:
            chunks.append(current.strip())
            current = paragraph
        else:
            current = f"{current}\n{paragraph}".strip()
    if current:
        chunks.append(current.strip())
    return chunks


def _clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def _clean_title(title: str) -> str:
    return _clean_line(title).strip(" .:-")


def _looks_like_toc_noise(title: str) -> bool:
    lowered = title.lower()
    return lowered in {"table of contents", "contents"} or lowered.startswith("page ")


def _is_title_like(line: str) -> bool:
    if len(line) < 4 or len(line) > 90:
        return False
    if line.endswith(".") or ":" in line:
        return False
    lowered = line.lower()
    if lowered in {"table of contents", "contents"}:
        return False
    if lowered.startswith("section "):
        return False
    if re.search(r"\d\s*/\s*\d", line):
        return False
    words = line.split()
    if len(words) > 9:
        return False
    return line[0].isupper() and any(char.isalpha() for char in line)


def _is_roman(value: str) -> bool:
    return bool(re.fullmatch(r"(?=[IVXLCDM]+$)M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})", value.upper()))
