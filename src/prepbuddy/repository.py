"""Repository layer for PrepBuddy's SQLite knowledge base."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import delete, desc, func, select
from sqlalchemy.orm import Session

from .db import (
    AnswerChoiceModel,
    AnswerModel,
    Base,
    DocumentModel,
    GenerationEventModel,
    KBSnapshotModel,
    PrepSessionModel,
    QuestionModel,
    SectionAliasModel,
    SectionChunkModel,
    SectionModel,
    SessionSectionModel,
    TopicStatModel,
    make_engine,
    make_session_factory,
    utcnow,
)
from .mapping import normalize_alias
from .path_utils import windows_display_path, wsl_display_path
from .schemas import (
    AdaptationContext,
    AnswerChoice,
    AnswerResult,
    Document,
    GeneratedSession,
    KBSnapshot,
    MCQ,
    ParsedSection,
    ProviderResult,
    Section,
    SectionChunk,
    SectionMapping,
    SessionSummary,
    SessionResult,
)


class PrepRepository:
    """SQLite-backed repository for documents, sessions, answers, and snapshots."""

    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        self._ensure_sqlite_parent(db_url)
        self.engine = make_engine(db_url)
        Base.metadata.create_all(self.engine)
        self._migrate_schema()
        self.SessionLocal = make_session_factory(self.engine)

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Yield a DB session and commit or rollback atomically."""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def save_document(
        self,
        *,
        path: Path,
        title: str,
        page_count: int,
        content_hash: str,
        sections: list[ParsedSection],
        original_filename: str | None = None,
        stored_path: Path | None = None,
        source_path: Path | None = None,
        is_managed_upload: bool = False,
    ) -> int:
        """Replace a document record and persist parsed sections, chunks, and aliases."""
        if not sections:
            raise ValueError("At least one section is required")
        alias_sets = self._build_alias_sets(sections)
        with self.session_scope() as session:
            existing = session.execute(select(DocumentModel).where(DocumentModel.path == str(path))).scalar_one_or_none()
            if existing:
                self._delete_sessions_for_document(session, existing.id)
                session.delete(existing)
                session.flush()
            document = DocumentModel(
                path=str(path),
                title=title,
                page_count=page_count,
                content_hash=content_hash,
                original_filename=original_filename or path.name,
                stored_path=str(stored_path or path),
                source_path=str(source_path or path),
                is_managed_upload=is_managed_upload,
            )
            session.add(document)
            session.flush()
            for parsed in sections:
                section = SectionModel(
                    document_id=document.id,
                    canonical_id=parsed.canonical_id,
                    source_label=parsed.source_label,
                    title=parsed.title,
                    text=parsed.text,
                    page_start=parsed.page_start,
                    page_end=parsed.page_end,
                )
                session.add(section)
                session.flush()
                for alias in sorted(alias_sets[parsed.canonical_id]):
                    session.add(SectionAliasModel(document_id=document.id, section_id=section.id, alias=alias))
                for chunk in parsed.chunks:
                    session.add(
                        SectionChunkModel(
                            section_id=section.id,
                            chunk_index=chunk.chunk_index,
                            chunk_id=f"doc{document.id}:{chunk.chunk_id}",
                            text=chunk.text,
                            page_start=chunk.page_start,
                            page_end=chunk.page_end,
                        )
                    )
            session.flush()
            return document.id

    def find_document_by_hash(self, content_hash: str) -> Document | None:
        """Return an existing document with the same content hash, if present."""
        with self.session_scope() as session:
            model = session.execute(
                select(DocumentModel).where(DocumentModel.content_hash == content_hash).order_by(DocumentModel.id)
            ).scalars().first()
            return self._document_schema(session, model) if model else None

    def list_documents(self) -> list[Document]:
        """List all ingested documents with section and session counts."""
        with self.session_scope() as session:
            models = session.execute(select(DocumentModel).order_by(DocumentModel.id)).scalars()
            return [self._document_schema(session, model) for model in models]

    def get_document(self, document_id: int | None = None) -> Document:
        """Return one document, defaulting to the latest ingested document."""
        document_id = document_id or self.latest_document_id()
        with self.session_scope() as session:
            model = session.get(DocumentModel, document_id)
            if model is None:
                raise ValueError(f"Unknown document: {document_id}")
            return self._document_schema(session, model)

    def latest_document_id(self) -> int:
        """Return the most recently ingested document ID."""
        with self.session_scope() as session:
            document_id = session.execute(select(func.max(DocumentModel.id))).scalar_one_or_none()
            if document_id is None:
                raise ValueError("No document has been ingested yet")
            return int(document_id)

    def list_sections(self, document_id: int | None = None) -> list[Section]:
        """List sections for a document, defaulting to the latest document."""
        document_id = document_id or self.latest_document_id()
        with self.session_scope() as session:
            models = session.execute(
                select(SectionModel)
                .where(SectionModel.document_id == document_id)
                .order_by(SectionModel.canonical_id)
            ).scalars()
            return [self._section_schema(model) for model in models]

    def list_mapping(self, document_id: int | None = None) -> list[SectionMapping]:
        """Return reviewer-facing section mapping records."""
        return [
            SectionMapping(
                canonical_id=section.canonical_id,
                source_label=section.source_label,
                title=section.title,
                page_range=f"{section.page_start}-{section.page_end}",
                aliases=section.aliases,
            )
            for section in self.list_sections(document_id)
        ]

    def export_mapping(self, *, data_dir: Path, docs_dir: Path, document_id: int | None = None) -> None:
        """Write machine- and human-readable mapping artifacts for reviewers."""
        resolved_document_id = document_id or self.latest_document_id()
        mapping = self.list_mapping(resolved_document_id)
        data_dir.mkdir(parents=True, exist_ok=True)
        docs_dir.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in mapping]
        (data_dir / "section_mapping.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        mapping_data_dir = data_dir / "mappings"
        mapping_data_dir.mkdir(parents=True, exist_ok=True)
        (mapping_data_dir / f"document_{resolved_document_id}.json").write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )
        lines = [
            "# Section Mapping",
            "",
            "Canonical IDs are the reviewer-facing IDs used by `prepbuddy` commands.",
            "",
            "| Canonical ID | Source Label | Title | Pages | Aliases |",
            "| ---: | --- | --- | --- | --- |",
        ]
        for item in mapping:
            aliases = ", ".join(item.aliases)
            lines.append(
                f"| {item.canonical_id} | {item.source_label} | {item.title} | {item.page_range} | {aliases} |"
            )
        (docs_dir / "section_mapping.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        mapping_docs_dir = docs_dir / "mappings"
        mapping_docs_dir.mkdir(parents=True, exist_ok=True)
        (mapping_docs_dir / f"document_{resolved_document_id}.md").write_text(
            "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    def find_section_by_token(self, token: str | int, *, document_id: int | None = None) -> Section:
        """Resolve one section token by canonical ID, source label, title, or alias."""
        document_id = document_id or self.latest_document_id()
        requested = str(token).strip()
        normalized = normalize_alias(requested)
        candidates = []
        for section in self.list_sections(document_id):
            if requested.isdigit() and int(requested) == section.canonical_id:
                candidates.append(section)
                continue
            if normalized in set(section.aliases):
                candidates.append(section)
        unique = {section.id: section for section in candidates}
        if not unique:
            raise ValueError(f"Unknown section identifier: {token}")
        if len(unique) > 1:
            raise ValueError(f"Ambiguous section identifier: {token}")
        return next(iter(unique.values()))

    def section_context(self, section_ids: list[int]) -> list[tuple[Section, list[SectionChunk], str]]:
        """Load section metadata, chunks, and full text for generation context."""
        with self.session_scope() as session:
            models = session.execute(
                select(SectionModel).where(SectionModel.id.in_(section_ids)).order_by(SectionModel.canonical_id)
            ).scalars()
            contexts = []
            for model in models:
                section = self._section_schema(model)
                chunks = [
                    SectionChunk(
                        id=chunk.id,
                        section_id=chunk.section_id,
                        chunk_index=chunk.chunk_index,
                        chunk_id=chunk.chunk_id,
                        text=chunk.text,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                    )
                    for chunk in model.chunks
                ]
                contexts.append((section, chunks, model.text))
            return contexts

    def create_generated_session(
        self,
        *,
        sections: list[Section],
        questions: list[MCQ],
        provider_result: ProviderResult,
        adaptation_context: AdaptationContext,
    ) -> GeneratedSession:
        """Persist generated questions before answers are submitted."""
        if not questions:
            raise ValueError("Cannot create a session without questions")
        document_ids = {section.document_id for section in sections}
        if len(document_ids) != 1:
            raise ValueError("All selected sections must belong to one document")
        document_id = next(iter(document_ids))
        session_id = str(uuid.uuid4())
        canonical_to_section = {section.canonical_id: section for section in sections}
        created_at = utcnow()
        with self.session_scope() as db:
            model = PrepSessionModel(
                id=session_id,
                document_id=document_id,
                status="generated",
                provider=provider_result.provider,
                model=provider_result.model,
                adaptation_context_json=adaptation_context.model_dump_json(),
                created_at=created_at,
            )
            db.add(model)
            db.flush()
            for section in sections:
                db.add(
                    SessionSectionModel(
                        session_id=session_id,
                        section_id=section.id,
                        section_canonical_id=section.canonical_id,
                    )
                )
            for order, question in enumerate(questions, start=1):
                section = canonical_to_section.get(question.section_id)
                if section is None:
                    raise ValueError(f"Question references unselected section {question.section_id}")
                question_id = question.id or str(uuid.uuid4())
                question.id = question_id
                q_model = QuestionModel(
                    id=question_id,
                    session_id=session_id,
                    section_id=section.id,
                    section_canonical_id=section.canonical_id,
                    created_order=order,
                    topic=question.topic,
                    question=question.question,
                    correct_answer=question.correct_answer,
                    explanation=question.explanation,
                    fingerprint=question.fingerprint or "",
                    source_chunk_ids_json=json.dumps(question.source_chunk_ids),
                )
                db.add(q_model)
                for choice in question.choices:
                    db.add(
                        AnswerChoiceModel(
                            question_id=question_id,
                            label=choice.label,
                            text=choice.text,
                            is_correct=choice.label == question.correct_answer,
                        )
                    )
            db.add(
                GenerationEventModel(
                    session_id=session_id,
                    provider=provider_result.provider,
                    model=provider_result.model,
                    latency_ms=provider_result.latency_ms,
                    token_usage_json=json.dumps(provider_result.token_usage),
                    warnings_json=json.dumps(provider_result.warnings),
                )
            )
        return self.get_generated_session(session_id)

    def get_generated_session(self, session_id: str) -> GeneratedSession:
        """Return a generated or completed session with questions."""
        with self.session_scope() as db:
            session = self._session_or_raise(db, session_id)
            questions = [self._mcq_schema(question) for question in session.questions]
            return GeneratedSession(
                session_id=session.id,
                document_id=session.document_id,
                sections=[item.section_canonical_id for item in session.sections],
                status=session.status,  # type: ignore[arg-type]
                questions=questions,
                provider_result=ProviderResult(provider=session.provider, model=session.model),
                adaptation_context=AdaptationContext.model_validate_json(session.adaptation_context_json),
                score=session.score,
                total=session.total,
                created_at=session.created_at,
            )

    def complete_session(self, session_id: str, answers: dict[str, str]) -> SessionResult:
        """Score and persist answers for a generated session."""
        with self.session_scope() as db:
            session = self._session_or_raise(db, session_id)
            if session.status == "completed":
                raise ValueError(f"Session {session_id} is already completed")
            score = 0
            results: list[AnswerResult] = []
            topic_stats_cache: dict[tuple[int, str], TopicStatModel] = {}
            for question in session.questions:
                selected = answers.get(question.id)
                if selected not in {"A", "B", "C", "D"}:
                    raise ValueError(f"Missing or invalid answer for question {question.id}")
                is_correct = selected == question.correct_answer
                if is_correct:
                    score += 1
                clarification = "Correct." if is_correct else question.explanation
                db.add(
                    AnswerModel(
                        question_id=question.id,
                        selected_answer=selected,
                        is_correct=is_correct,
                        clarification=clarification,
                    )
                )
                self._update_topic_stats(db, question, is_correct, topic_stats_cache)
                results.append(
                    AnswerResult(
                        question_id=question.id,
                        question_number=question.created_order,
                        selected_answer=selected,
                        correct_answer=question.correct_answer,
                        is_correct=is_correct,
                        clarification=clarification,
                    )
                )
            session.status = "completed"
            session.score = score
            session.total = len(session.questions)
            session.completed_at = utcnow()
            db.flush()
            snapshot = self._snapshot_payload(db, limit=5, document_id=session.document_id)
            db.add(
                KBSnapshotModel(
                    session_id=session.id,
                    payload_json=json.dumps(snapshot.model_dump(mode="json"), indent=2),
                )
            )
            questions = [self._mcq_schema(question) for question in session.questions]
            return SessionResult(
                session_id=session.id,
                document_id=session.document_id,
                sections=[item.section_canonical_id for item in session.sections],
                score=score,
                total=len(session.questions),
                results=results,
                adaptation_context=AdaptationContext.model_validate_json(session.adaptation_context_json),
                questions=questions,
            )

    def list_sessions(self, document_id: int | None = None) -> list[SessionSummary]:
        """List persisted sessions, optionally scoped to one document."""
        with self.session_scope() as db:
            query = select(PrepSessionModel).order_by(desc(PrepSessionModel.created_at))
            if document_id is not None:
                query = query.where(PrepSessionModel.document_id == document_id)
            return [self._session_summary(model) for model in db.execute(query).scalars()]

    def delete_session(self, session_id: str) -> None:
        """Delete one session and its dependent generation/snapshot records."""
        with self.session_scope() as db:
            self._delete_session_by_id(db, session_id)

    def delete_document(self, document_id: int, *, uploads_dir: Path | None = None, delete_file: bool = True) -> None:
        """Delete a document, dependent prep state, and its managed upload file."""
        file_to_delete: Path | None = None
        with self.session_scope() as db:
            document = db.get(DocumentModel, document_id)
            if document is None:
                raise ValueError(f"Unknown document: {document_id}")
            if delete_file and document.is_managed_upload and uploads_dir is not None:
                candidate = Path(document.stored_path or document.path)
                if self._is_within(candidate, uploads_dir):
                    file_to_delete = candidate
            self._delete_sessions_for_document(db, document_id)
            db.execute(delete(TopicStatModel).where(TopicStatModel.document_id == document_id))
            db.delete(document)
        if file_to_delete and file_to_delete.exists():
            file_to_delete.unlink()

    def weak_topics(self, section_ids: list[int], *, document_id: int | None = None) -> list[dict[str, object]]:
        """Return historically weak topics for selected canonical section IDs."""
        if not section_ids:
            return []
        with self.session_scope() as db:
            query = (
                select(TopicStatModel)
                .where(TopicStatModel.section_canonical_id.in_(section_ids), TopicStatModel.wrong > 0)
                .order_by(desc(TopicStatModel.wrong), desc(TopicStatModel.attempts), TopicStatModel.topic)
            )
            if document_id is not None:
                query = query.where(TopicStatModel.document_id == document_id)
            stats = db.execute(query).scalars()
            return [
                {
                    "section_id": stat.section_canonical_id,
                    "topic": stat.topic,
                    "attempts": stat.attempts,
                    "wrong": stat.wrong,
                    "correct": stat.correct,
                }
                for stat in stats
            ]

    def prior_session_count(self, section_ids: list[int], *, document_id: int | None = None) -> int:
        """Count completed prior sessions involving any selected canonical section."""
        if not section_ids:
            return 0
        with self.session_scope() as db:
            query = (
                select(func.count(func.distinct(PrepSessionModel.id)))
                .join(SessionSectionModel)
                .where(
                    PrepSessionModel.status == "completed",
                    SessionSectionModel.section_canonical_id.in_(section_ids),
                )
            )
            if document_id is not None:
                query = query.where(PrepSessionModel.document_id == document_id)
            return int(db.execute(query).scalar_one())

    def recent_fingerprints(
        self,
        section_ids: list[int],
        *,
        document_id: int | None = None,
        limit: int = 50,
    ) -> list[str]:
        """Return recent completed question fingerprints for repetition avoidance."""
        if not section_ids:
            return []
        with self.session_scope() as db:
            query = (
                select(QuestionModel.fingerprint)
                .join(PrepSessionModel)
                .where(
                    PrepSessionModel.status == "completed",
                    QuestionModel.section_canonical_id.in_(section_ids),
                )
                .order_by(desc(PrepSessionModel.completed_at), QuestionModel.created_order)
                .limit(limit)
            )
            if document_id is not None:
                query = query.where(PrepSessionModel.document_id == document_id)
            rows = db.execute(query).scalars()
            return [fingerprint for fingerprint in rows if fingerprint]

    def snapshot(self, *, limit: int = 5, document_id: int | None = None) -> KBSnapshot:
        """Return a human-readable snapshot of recent completed sessions."""
        with self.session_scope() as db:
            return self._snapshot_payload(db, limit=limit, document_id=document_id)

    def session_payload(self, session_id: str) -> dict[str, object]:
        """Return a JSON-serializable session payload for scenario exports."""
        generated = self.get_generated_session(session_id)
        return generated.model_dump(mode="json")

    def _snapshot_payload(self, db: Session, *, limit: int, document_id: int | None = None) -> KBSnapshot:
        query = (
            select(PrepSessionModel)
            .where(PrepSessionModel.status == "completed")
            .order_by(desc(PrepSessionModel.completed_at), desc(PrepSessionModel.created_at))
            .limit(limit)
        )
        if document_id is not None:
            query = query.where(PrepSessionModel.document_id == document_id)
        sessions = db.execute(query).scalars()
        payload: list[dict[str, object]] = []
        for session in sessions:
            question_records = []
            for question in session.questions:
                question_records.append(
                    {
                        "question_id": question.id,
                        "question_number": question.created_order,
                        "section_id": question.section_canonical_id,
                        "topic": question.topic,
                        "question": question.question,
                        "correct_answer": question.correct_answer,
                        "selected_answer": question.answer.selected_answer if question.answer else None,
                        "is_correct": question.answer.is_correct if question.answer else None,
                        "clarification": question.answer.clarification if question.answer else None,
                    }
                )
            payload.append(
                {
                    "session_id": session.id,
                    "document_id": session.document_id,
                    "document_title": session.document.title if session.document else None,
                    "status": session.status,
                    "provider": session.provider,
                    "created_at": session.created_at.isoformat(),
                    "completed_at": session.completed_at.isoformat() if session.completed_at else None,
                    "sections": [item.section_canonical_id for item in session.sections],
                    "score": session.score,
                    "total": session.total,
                    "questions": question_records,
                }
            )
        return KBSnapshot(generated_at=datetime.now(timezone.utc), sessions=payload)

    def _update_topic_stats(
        self,
        db: Session,
        question: QuestionModel,
        is_correct: bool,
        topic_stats_cache: dict[tuple[int, str], TopicStatModel],
    ) -> None:
        key = (question.section_id, question.topic)
        stat = topic_stats_cache.get(key)
        if stat is None:
            stat = db.execute(
                select(TopicStatModel).where(
                    TopicStatModel.section_id == question.section_id,
                    TopicStatModel.topic == question.topic,
                )
            ).scalar_one_or_none()
        if stat is None:
            stat = TopicStatModel(
                document_id=question.section.document_id,
                section_id=question.section_id,
                section_canonical_id=question.section_canonical_id,
                topic=question.topic,
                attempts=0,
                correct=0,
                wrong=0,
            )
            db.add(stat)
        topic_stats_cache[key] = stat
        stat.attempts += 1
        if is_correct:
            stat.correct += 1
        else:
            stat.wrong += 1
        stat.last_seen_at = utcnow()

    def _session_or_raise(self, db: Session, session_id: str) -> PrepSessionModel:
        session = db.get(PrepSessionModel, session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        return session

    def _section_schema(self, model: SectionModel) -> Section:
        return Section(
            id=model.id,
            document_id=model.document_id,
            canonical_id=model.canonical_id,
            source_label=model.source_label,
            title=model.title,
            page_start=model.page_start,
            page_end=model.page_end,
            aliases=[alias.alias for alias in model.aliases],
            chunk_count=len(model.chunks),
        )

    def _mcq_schema(self, model: QuestionModel) -> MCQ:
        return MCQ(
            id=model.id,
            question_number=model.created_order,
            section_id=model.section_canonical_id,
            topic=model.topic,
            question=model.question,
            choices=[AnswerChoice(label=choice.label, text=choice.text) for choice in model.choices],  # type: ignore[arg-type]
            correct_answer=model.correct_answer,  # type: ignore[arg-type]
            explanation=model.explanation,
            source_chunk_ids=json.loads(model.source_chunk_ids_json),
            fingerprint=model.fingerprint,
        )

    def _session_summary(self, model: PrepSessionModel) -> SessionSummary:
        return SessionSummary(
            id=model.id,
            document_id=model.document_id,
            status=model.status,  # type: ignore[arg-type]
            provider=model.provider,
            model=model.model,
            score=model.score,
            total=model.total,
            sections=[item.section_canonical_id for item in model.sections],
            question_count=len(model.questions),
            created_at=model.created_at,
            completed_at=model.completed_at,
        )

    def _document_schema(self, db: Session, model: DocumentModel) -> Document:
        section_count = int(
            db.execute(select(func.count(SectionModel.id)).where(SectionModel.document_id == model.id)).scalar_one()
        )
        session_count = int(
            db.execute(select(func.count(PrepSessionModel.id)).where(PrepSessionModel.document_id == model.id)).scalar_one()
        )
        display_path = model.stored_path or model.path
        return Document(
            id=model.id,
            path=model.path,
            title=model.title,
            page_count=model.page_count,
            content_hash=model.content_hash,
            created_at=model.created_at,
            original_filename=model.original_filename,
            stored_path=model.stored_path,
            source_path=model.source_path,
            is_managed_upload=model.is_managed_upload,
            section_count=section_count,
            session_count=session_count,
            windows_path=windows_display_path(display_path),
            wsl_path=wsl_display_path(display_path),
        )

    def _delete_sessions_for_document(self, db: Session, document_id: int) -> None:
        session_ids = db.execute(
            select(PrepSessionModel.id).where(PrepSessionModel.document_id == document_id)
        ).scalars()
        for session_id in list(session_ids):
            self._delete_session_by_id(db, session_id)

    def _delete_session_by_id(self, db: Session, session_id: str) -> None:
        session = db.get(PrepSessionModel, session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        db.execute(delete(GenerationEventModel).where(GenerationEventModel.session_id == session_id))
        db.execute(delete(KBSnapshotModel).where(KBSnapshotModel.session_id == session_id))
        db.delete(session)

    def _is_within(self, path: Path, parent: Path) -> bool:
        try:
            path.resolve(strict=False).relative_to(parent.resolve(strict=False))
            return True
        except ValueError:
            return False

    def _build_alias_sets(self, sections: list[ParsedSection]) -> dict[int, set[str]]:
        seen: dict[str, int] = {}
        alias_sets: dict[int, set[str]] = {}
        for section in sections:
            aliases = {
                normalize_alias(section.canonical_id),
                normalize_alias(section.source_label),
                normalize_alias(section.title),
                normalize_alias(f"section {section.canonical_id}"),
            }
            if section.source_label.lower().startswith("section "):
                aliases.add(normalize_alias(section.source_label.replace("Section ", "")))
            aliases = {alias for alias in aliases if alias}
            for alias in aliases:
                previous = seen.get(alias)
                if previous is not None and previous != section.canonical_id:
                    raise ValueError(f"Duplicate section alias '{alias}' for sections {previous} and {section.canonical_id}")
                seen[alias] = section.canonical_id
            alias_sets[section.canonical_id] = aliases
        return alias_sets

    def _ensure_sqlite_parent(self, db_url: str) -> None:
        if not db_url.startswith("sqlite:///"):
            return
        raw_path = db_url.removeprefix("sqlite:///")
        if raw_path in {":memory:", ""}:
            return
        Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)

    def _migrate_schema(self) -> None:
        """Apply additive SQLite migrations for local development databases."""
        with self.engine.begin() as connection:
            tables = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "documents" in tables:
                document_columns = {
                    row[1] for row in connection.exec_driver_sql("PRAGMA table_info(documents)").fetchall()
                }
                if "original_filename" not in document_columns:
                    connection.exec_driver_sql("ALTER TABLE documents ADD COLUMN original_filename VARCHAR(300)")
                if "stored_path" not in document_columns:
                    connection.exec_driver_sql("ALTER TABLE documents ADD COLUMN stored_path VARCHAR(500)")
                if "source_path" not in document_columns:
                    connection.exec_driver_sql("ALTER TABLE documents ADD COLUMN source_path VARCHAR(500)")
                if "is_managed_upload" not in document_columns:
                    connection.exec_driver_sql(
                        "ALTER TABLE documents ADD COLUMN is_managed_upload BOOLEAN NOT NULL DEFAULT 0"
                    )
                connection.exec_driver_sql(
                    "UPDATE documents SET original_filename = COALESCE(original_filename, path), "
                    "stored_path = COALESCE(stored_path, path), source_path = COALESCE(source_path, path)"
                )
            if "prep_sessions" in tables:
                session_columns = {
                    row[1] for row in connection.exec_driver_sql("PRAGMA table_info(prep_sessions)").fetchall()
                }
                if "document_id" not in session_columns:
                    connection.exec_driver_sql("ALTER TABLE prep_sessions ADD COLUMN document_id INTEGER")
                connection.exec_driver_sql(
                    """
                    UPDATE prep_sessions
                    SET document_id = (
                        SELECT sections.document_id
                        FROM session_sections
                        JOIN sections ON sections.id = session_sections.section_id
                        WHERE session_sections.session_id = prep_sessions.id
                        LIMIT 1
                    )
                    WHERE document_id IS NULL
                    """
                )
