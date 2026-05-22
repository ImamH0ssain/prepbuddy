"""SQLAlchemy database models and session helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base for PrepBuddy ORM models."""


class DocumentModel(Base):
    """A source PDF ingested into the knowledge base."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(300), nullable=True)
    stored_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_managed_upload: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    sections: Mapped[list["SectionModel"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="SectionModel.canonical_id",
    )


class SectionModel(Base):
    """A top-level source section."""

    __tablename__ = "sections"
    __table_args__ = (UniqueConstraint("document_id", "canonical_id", name="uq_section_document_canonical"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    canonical_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_label: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)

    document: Mapped[DocumentModel] = relationship(back_populates="sections")
    aliases: Mapped[list["SectionAliasModel"]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="SectionAliasModel.alias",
    )
    chunks: Mapped[list["SectionChunkModel"]] = relationship(
        back_populates="section",
        cascade="all, delete-orphan",
        order_by="SectionChunkModel.chunk_index",
    )


class SectionAliasModel(Base):
    """Normalized alias for resolving user-facing section tokens."""

    __tablename__ = "section_aliases"
    __table_args__ = (UniqueConstraint("document_id", "alias", name="uq_alias_document_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    alias: Mapped[str] = mapped_column(String(300), nullable=False)

    section: Mapped[SectionModel] = relationship(back_populates="aliases")


class SectionChunkModel(Base):
    """Smaller text chunk used for LLM grounding."""

    __tablename__ = "section_chunks"
    __table_args__ = (UniqueConstraint("section_id", "chunk_index", name="uq_chunk_section_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_id: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)

    section: Mapped[SectionModel] = relationship(back_populates="chunks")


class PrepSessionModel(Base):
    """A generated or completed preparation session."""

    __tablename__ = "prep_sessions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="generated")
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adaptation_context_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    document: Mapped[DocumentModel | None] = relationship()
    sections: Mapped[list["SessionSectionModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="SessionSectionModel.section_canonical_id",
    )
    questions: Mapped[list["QuestionModel"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="QuestionModel.created_order",
    )


class SessionSectionModel(Base):
    """Join table from sessions to selected sections."""

    __tablename__ = "session_sections"
    __table_args__ = (UniqueConstraint("session_id", "section_id", name="uq_session_section"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), nullable=False)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    section_canonical_id: Mapped[int] = mapped_column(Integer, nullable=False)

    session: Mapped[PrepSessionModel] = relationship(back_populates="sections")
    section: Mapped[SectionModel] = relationship()


class QuestionModel(Base):
    """A generated MCQ persisted in the KB."""

    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), nullable=False)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    section_canonical_id: Mapped[int] = mapped_column(Integer, nullable=False)
    created_order: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    correct_answer: Mapped[str] = mapped_column(String(1), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_chunk_ids_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")

    session: Mapped[PrepSessionModel] = relationship(back_populates="questions")
    section: Mapped[SectionModel] = relationship()
    choices: Mapped[list["AnswerChoiceModel"]] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        order_by="AnswerChoiceModel.label",
    )
    answer: Mapped["AnswerModel | None"] = relationship(
        back_populates="question",
        cascade="all, delete-orphan",
        uselist=False,
    )


class AnswerChoiceModel(Base):
    """Persisted answer choice for a generated MCQ."""

    __tablename__ = "answer_choices"
    __table_args__ = (UniqueConstraint("question_id", "label", name="uq_choice_question_label"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    label: Mapped[str] = mapped_column(String(1), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    question: Mapped[QuestionModel] = relationship(back_populates="choices")


class AnswerModel(Base):
    """A user's answer to one persisted MCQ."""

    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), nullable=False, unique=True)
    selected_answer: Mapped[str] = mapped_column(String(1), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    clarification: Mapped[str] = mapped_column(Text, nullable=False)
    answered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    question: Mapped[QuestionModel] = relationship(back_populates="answer")


class TopicStatModel(Base):
    """Aggregated topic performance for adaptive weighting."""

    __tablename__ = "topic_stats"
    __table_args__ = (UniqueConstraint("section_id", "topic", name="uq_topic_section_topic"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    section_id: Mapped[int] = mapped_column(ForeignKey("sections.id", ondelete="CASCADE"), nullable=False)
    section_canonical_id: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(String(300), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    wrong: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class KBSnapshotModel(Base):
    """Saved snapshot of recent KB state after a session completes."""

    __tablename__ = "kb_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class GenerationEventModel(Base):
    """LLM generation metadata for post-run review."""

    __tablename__ = "generation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("prep_sessions.id", ondelete="CASCADE"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_usage_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    warnings_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


class AppStateModel(Base):
    """Small key-value store for application-wide maintenance state."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


def make_engine(db_url: str) -> Engine:
    """Create a SQLite-friendly SQLAlchemy engine."""
    engine = create_engine(db_url, future=True)

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create the configured session factory."""
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
