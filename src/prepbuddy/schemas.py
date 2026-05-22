"""Pydantic schemas used across ingestion, generation, sessions, and APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ParsedChunk(BaseModel):
    """A section chunk extracted from a PDF."""

    chunk_index: int
    chunk_id: str
    text: str
    page_start: int
    page_end: int


class ParsedSection(BaseModel):
    """A top-level PDF section before database persistence."""

    canonical_id: int
    source_label: str
    title: str
    text: str
    page_start: int
    page_end: int
    chunks: list[ParsedChunk] = Field(default_factory=list)


class Document(BaseModel):
    """An ingested PDF available for preparation sessions."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    path: str
    title: str
    page_count: int
    content_hash: str
    created_at: datetime
    original_filename: str | None = None
    stored_path: str | None = None
    source_path: str | None = None
    is_managed_upload: bool = False
    section_count: int = 0
    session_count: int = 0
    windows_path: str = ""
    wsl_path: str = ""


class Section(BaseModel):
    """A stored section with aliases and chunk metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    canonical_id: int
    source_label: str
    title: str
    page_start: int
    page_end: int
    aliases: list[str]
    chunk_count: int


class SectionMapping(BaseModel):
    """Human- and machine-readable mapping from canonical IDs to source headings."""

    canonical_id: int
    source_label: str
    title: str
    page_range: str
    aliases: list[str]


class SectionChunk(BaseModel):
    """A persisted section chunk used as LLM context."""

    id: int
    section_id: int
    chunk_index: int
    chunk_id: str
    text: str
    page_start: int
    page_end: int


class AnswerChoice(BaseModel):
    """One MCQ answer choice."""

    label: Literal["A", "B", "C", "D"]
    text: str


class MCQ(BaseModel):
    """A generated multiple-choice question."""

    id: str | None = None
    question_number: int | None = None
    section_id: int
    topic: str
    question: str
    choices: list[AnswerChoice]
    correct_answer: Literal["A", "B", "C", "D"]
    explanation: str
    source_chunk_ids: list[str] = Field(default_factory=list)
    fingerprint: str | None = None

    @field_validator("choices")
    @classmethod
    def require_four_choices(cls, choices: list[AnswerChoice]) -> list[AnswerChoice]:
        """Validate the assessment requirement of four choices per question."""
        labels = [choice.label for choice in choices]
        if labels != ["A", "B", "C", "D"]:
            raise ValueError("MCQs must contain choices A, B, C, and D in order")
        return choices


class ProviderResult(BaseModel):
    """Provider metadata captured for logs and review."""

    provider: str
    model: str
    latency_ms: int = 0
    token_usage: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class AdaptationContext(BaseModel):
    """Historical context used to adapt future question generation."""

    prior_session_count: int = 0
    weak_topics: list[dict[str, Any]] = Field(default_factory=list)
    avoid_fingerprints: list[str] = Field(default_factory=list)


class GenerationSection(BaseModel):
    """Section context passed to an LLM provider."""

    canonical_id: int
    source_label: str
    title: str
    text: str
    chunk_ids: list[str]


class GenerationRequest(BaseModel):
    """Structured request for MCQ generation."""

    sections: list[GenerationSection]
    questions_per_section: int
    adaptation_context: AdaptationContext


class GeneratedQuestionSet(BaseModel):
    """Structured MCQ generation response."""

    questions: list[MCQ]
    provider_result: ProviderResult


class GeneratedSession(BaseModel):
    """A generated session before or after answer submission."""

    session_id: str
    document_id: int | None = None
    sections: list[int]
    status: Literal["generated", "completed"]
    questions: list[MCQ]
    provider_result: ProviderResult
    adaptation_context: AdaptationContext
    score: int | None = None
    total: int | None = None
    created_at: datetime


class AnswerResult(BaseModel):
    """Question-level result after scoring."""

    question_id: str
    question_number: int | None = None
    selected_answer: str
    correct_answer: str
    is_correct: bool
    clarification: str


class SessionResult(BaseModel):
    """Completed session result returned by CLI and API."""

    session_id: str
    document_id: int | None = None
    sections: list[int]
    score: int
    total: int
    results: list[AnswerResult]
    adaptation_context: AdaptationContext
    questions: list[MCQ]


class KBSnapshot(BaseModel):
    """Human-readable snapshot of recent KB state."""

    generated_at: datetime
    sessions: list[dict[str, Any]]


class SessionSummary(BaseModel):
    """Compact persisted session summary for document/session drawers."""

    id: str
    document_id: int | None = None
    status: Literal["generated", "completed"]
    provider: str
    model: str
    score: int | None = None
    total: int | None = None
    sections: list[int] = Field(default_factory=list)
    question_count: int = 0
    created_at: datetime
    completed_at: datetime | None = None
