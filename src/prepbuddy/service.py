"""Application service layer shared by CLI, API, and UI."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .ingestion import parse_pdf
from .logging_utils import configure_logging, log_event
from .mapping import SectionResolver, load_mapping_override
from .path_utils import managed_upload_path
from .providers import LLMProvider, make_provider, question_fingerprint
from .repository import PrepRepository
from .schemas import (
    AdaptationContext,
    Document,
    GeneratedQuestionSet,
    GeneratedSession,
    GenerationRequest,
    GenerationSection,
    MCQ,
    Section,
    SessionResult,
    SessionSummary,
)
from .settings import Settings


class PrepService:
    """Coordinates ingestion, generation, scoring, adaptation, and exports."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        repository: PrepRepository | None = None,
        provider: LLMProvider | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.settings.ensure_dirs()
        configure_logging(self.settings.logs_dir)
        self.repository = repository or PrepRepository(self.settings.db_url)
        self.provider = provider

    def ingest_pdf(self, pdf_path: Path | None = None) -> int:
        """Parse a PDF, persist sections, and export section mapping docs."""
        path = pdf_path or self.settings.default_pdf_path
        title, page_count, content_hash, sections = parse_pdf(path)
        document_id = self.repository.save_document(
            path=path,
            title=title,
            page_count=page_count,
            content_hash=content_hash,
            sections=sections,
            original_filename=path.name,
            stored_path=path,
            source_path=path,
            is_managed_upload=False,
        )
        self.repository.export_mapping(data_dir=self.settings.data_dir, docs_dir=self.settings.docs_dir, document_id=document_id)
        log_event("document_ingested", document_id=document_id, pdf_path=str(path), section_count=len(sections))
        return document_id

    def ingest_uploaded_pdf(self, filename: str, content: bytes) -> int:
        """Store an uploaded PDF in the managed library and ingest it if new."""
        if not content:
            raise ValueError("Uploaded PDF is empty")
        upload_hash = hashlib.sha256(content).hexdigest()
        existing = self.repository.find_document_by_hash(upload_hash)
        if existing is not None:
            if existing.deleted_at is not None:
                upload_dir = self.settings.data_dir / "uploads"
                upload_dir.mkdir(parents=True, exist_ok=True)
                stored_path = managed_upload_path(upload_dir, filename, content)
                stored_path.write_bytes(content)
                self.repository.reactivate_document(
                    existing.id,
                    path=stored_path,
                    original_filename=filename,
                    stored_path=stored_path,
                    source_path=stored_path,
                    is_managed_upload=True,
                )
                self.repository.export_mapping(
                    data_dir=self.settings.data_dir,
                    docs_dir=self.settings.docs_dir,
                    document_id=existing.id,
                )
                log_event("document_upload_reactivated", document_id=existing.id, pdf_path=str(stored_path))
                return existing.id
            log_event("document_upload_reused", document_id=existing.id, pdf_path=existing.stored_path)
            return existing.id
        upload_dir = self.settings.data_dir / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored_path = managed_upload_path(upload_dir, filename, content)
        stored_path.write_bytes(content)
        title, page_count, content_hash, sections = parse_pdf(stored_path)
        existing = self.repository.find_document_by_hash(content_hash)
        if existing is not None:
            if existing.deleted_at is not None:
                self.repository.reactivate_document(
                    existing.id,
                    path=stored_path,
                    original_filename=filename,
                    stored_path=stored_path,
                    source_path=stored_path,
                    is_managed_upload=True,
                )
                self.repository.export_mapping(
                    data_dir=self.settings.data_dir,
                    docs_dir=self.settings.docs_dir,
                    document_id=existing.id,
                )
                log_event("document_upload_reactivated", document_id=existing.id, pdf_path=str(stored_path))
                return existing.id
            if stored_path.exists() and str(stored_path) != (existing.stored_path or existing.path):
                stored_path.unlink()
            log_event("document_upload_reused", document_id=existing.id, pdf_path=str(stored_path))
            return existing.id
        document_id = self.repository.save_document(
            path=stored_path,
            title=title,
            page_count=page_count,
            content_hash=content_hash,
            sections=sections,
            original_filename=filename,
            stored_path=stored_path,
            source_path=stored_path,
            is_managed_upload=True,
        )
        self.repository.export_mapping(data_dir=self.settings.data_dir, docs_dir=self.settings.docs_dir, document_id=document_id)
        log_event("document_uploaded", document_id=document_id, pdf_path=str(stored_path), section_count=len(sections))
        return document_id

    def list_documents(self) -> list[Document]:
        """List all ingested PDFs."""
        return self.repository.list_documents()

    def list_sections(self, document_id: int | None = None) -> list[Section]:
        """List sections for a document, defaulting to latest."""
        return self.repository.list_sections(document_id=document_id)

    def list_sessions(self, document_id: int | None = None) -> list[SessionSummary]:
        """List sessions for a document, defaulting to all when omitted."""
        return self.repository.list_sessions(document_id=document_id)

    def create_session(
        self,
        sections: list[str | int],
        *,
        questions_per_section: int | None = None,
        provider_name: str | None = None,
        document_id: int | None = None,
    ) -> GeneratedSession:
        """Generate and persist an unanswered prep session."""
        count = questions_per_section or self.settings.questions_per_section
        if count < 1:
            raise ValueError("questions_per_section must be at least 1")
        resolved_sections = self._resolve_sections(sections, document_id=document_id)
        contexts = self._generation_sections(resolved_sections)
        active_document_id = resolved_sections[0].document_id
        adaptation_context = self._adaptation_context(
            [section.canonical_id for section in resolved_sections],
            document_id=active_document_id,
        )
        request = GenerationRequest(
            sections=contexts,
            questions_per_section=count,
            adaptation_context=adaptation_context,
        )
        provider = self._provider(provider_name)
        validated = self._generate_exact_question_set(provider, request, resolved_sections, adaptation_context)
        session = self.repository.create_generated_session(
            sections=resolved_sections,
            questions=validated.questions,
            provider_result=validated.provider_result,
            adaptation_context=adaptation_context,
        )
        log_event(
            "session_generated",
            session_id=session.session_id,
            sections=session.sections,
            provider=session.provider_result.provider,
            model=session.provider_result.model,
            question_count=len(session.questions),
            prior_session_count=session.adaptation_context.prior_session_count,
        )
        return session

    def submit_answers(self, session_id: str, answers: dict[str, str]) -> SessionResult:
        """Score answers and persist the completed session."""
        normalized = {question_id: answer.strip().upper() for question_id, answer in answers.items()}
        result = self.repository.complete_session(session_id, normalized)
        log_event("session_scored", session_id=session_id, score=result.score, total=result.total, sections=result.sections)
        return result

    def run_prep_session(
        self,
        sections: list[str | int],
        *,
        questions_per_section: int | None = None,
        provider_name: str | None = None,
        answers_mode: str = "simulate",
        document_id: int | None = None,
    ) -> SessionResult:
        """Run a complete prep session using simulated answers."""
        generated = self.create_session(
            sections,
            questions_per_section=questions_per_section,
            provider_name=provider_name,
            document_id=document_id,
        )
        if answers_mode != "simulate":
            raise ValueError("Only simulate mode is supported by the service; CLI handles interactive prompts")
        return self.submit_answers(generated.session_id, self.simulate_answers(generated.questions))

    def simulate_answers(self, questions: list[MCQ]) -> dict[str, str]:
        """Create a deterministic mix of correct and incorrect answers."""
        answers: dict[str, str] = {}
        for index, question in enumerate(questions):
            if question.id is None:
                continue
            should_miss = question.section_id == 8 or index % 4 == 0
            answers[question.id] = self._wrong_label(question.correct_answer) if should_miss else question.correct_answer
        return answers

    def run_scenario_a(
        self,
        *,
        sections: list[str | int],
        output_root: Path,
        questions_per_section: int | None = None,
        provider_name: str | None = None,
        document_id: int | None = None,
    ) -> SessionResult:
        """Run and export a cold-start scenario over selected sections."""
        result = self.run_prep_session(
            sections,
            questions_per_section=questions_per_section,
            provider_name=provider_name,
            answers_mode="simulate",
            document_id=document_id,
        )
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / "questions_scenario_a.json").write_text(
            json.dumps({"session": result.model_dump(mode="json")}, indent=2),
            encoding="utf-8",
        )
        (output_root / "kb_snapshot_scenario_a.json").write_text(
            self.repository.snapshot(limit=5).model_dump_json(indent=2),
            encoding="utf-8",
        )
        return result

    def run_scenario_b(
        self,
        *,
        output_root: Path | None = None,
        questions_per_section: int | None = None,
        provider_name: str | None = None,
        document_id: int | None = None,
    ) -> list[SessionResult]:
        """Run the required three-iteration Scenario B and export JSON artifacts."""
        root = output_root or self.settings.outputs_dir
        root.mkdir(parents=True, exist_ok=True)
        iterations = [(["5", "8"], 1), (["6", "8", "9"], 2), (["8"], 3)]
        results: list[SessionResult] = []
        for section_tokens, iteration in iterations:
            try:
                result = self.run_prep_session(
                    section_tokens,
                    questions_per_section=questions_per_section,
                    provider_name=provider_name or self.settings.llm_provider,
                    answers_mode="simulate",
                    document_id=document_id,
                )
            except ValueError as exc:
                raise ValueError(
                    "Scenario B requires resolvable reviewer-facing sections 5, 6, 8, and 9. "
                    "If the PDF uses different labels or has fewer top-level sections, add config/section_mapping.json."
                ) from exc
            results.append(result)
            iteration_dir = root / f"scenario_b_iter{iteration}"
            iteration_dir.mkdir(parents=True, exist_ok=True)
            (iteration_dir / f"questions_iter{iteration}.json").write_text(
                json.dumps({"session": result.model_dump(mode="json")}, indent=2),
                encoding="utf-8",
            )
            (iteration_dir / f"kb_snapshot_iter{iteration}.json").write_text(
                self.repository.snapshot(limit=5).model_dump_json(indent=2),
                encoding="utf-8",
            )
        return results

    def mapping_payload(self, document_id: int | None = None) -> list[dict[str, object]]:
        """Return a section mapping as JSON-friendly dictionaries."""
        return [item.model_dump(mode="json") for item in self.repository.list_mapping(document_id=document_id)]

    def delete_session(self, session_id: str) -> None:
        """Delete one persisted session."""
        self.repository.delete_session(session_id)

    def delete_document(self, document_id: int, *, delete_file: bool = True) -> None:
        """Archive a document while preserving sessions and adaptive history."""
        self.repository.delete_document(document_id, uploads_dir=self.settings.data_dir / "uploads", delete_file=delete_file)

    def delete_all_documents(self, *, delete_file: bool = True) -> None:
        """Archive all active documents while preserving their sessions."""
        self.repository.delete_all_documents(uploads_dir=self.settings.data_dir / "uploads", delete_file=delete_file)

    def delete_all_sessions(self) -> None:
        """Delete every prep session and derived adaptive records."""
        self.repository.delete_all_sessions()

    def clear_knowledge_base(self) -> None:
        """Reset adaptation data while preserving documents and session records."""
        self.repository.clear_knowledge_base()

    def clear_everything(self) -> None:
        """Hard-delete all app records and managed upload files."""
        self.repository.clear_everything(uploads_dir=self.settings.data_dir / "uploads")

    def _resolve_sections(self, sections: list[str | int], *, document_id: int | None = None) -> list[Section]:
        document_id = document_id or self.repository.latest_document_id()
        mapping_override = load_mapping_override(self.settings.mapping_file)
        resolver = SectionResolver(self.repository, document_id=document_id, mapping_override=mapping_override)
        return resolver.resolve_many(sections)

    def _generation_sections(self, sections: list[Section]) -> list[GenerationSection]:
        contexts = self.repository.section_context([section.id for section in sections])
        generated: list[GenerationSection] = []
        for section, chunks, full_text in contexts:
            generated.append(
                GenerationSection(
                    canonical_id=section.canonical_id,
                    source_label=section.source_label,
                    title=section.title,
                    text=full_text,
                    chunk_ids=[chunk.chunk_id for chunk in chunks],
                )
            )
        return generated

    def _adaptation_context(self, canonical_ids: list[int], *, document_id: int | None = None) -> AdaptationContext:
        return AdaptationContext(
            prior_session_count=self.repository.prior_session_count(canonical_ids, document_id=document_id),
            weak_topics=self.repository.weak_topics(canonical_ids, document_id=document_id),
            avoid_fingerprints=self.repository.recent_fingerprints(canonical_ids, document_id=document_id),
        )

    def _provider(self, provider_name: str | None) -> LLMProvider:
        selected = provider_name or self.settings.llm_provider
        if self.provider is not None and selected in {self.provider.name, "auto", None}:
            return self.provider
        return make_provider(self.settings, selected)

    def _generate_exact_question_set(
        self,
        provider: LLMProvider,
        request: GenerationRequest,
        sections: list[Section],
        adaptation_context: AdaptationContext,
    ) -> GeneratedQuestionSet:
        """Generate and repair MCQs until the exact requested count is met."""
        expected_by_section = {section.canonical_id: request.questions_per_section for section in sections}
        contexts_by_id = {section.canonical_id: context for section, context in zip(sections, request.sections)}
        total_expected = sum(expected_by_section.values())
        combined: list[MCQ] = []
        seen: set[str] = set()
        provider_result = None
        warnings: list[str] = []
        current_request = request
        max_attempts = 3

        for attempt in range(1, max_attempts + 1):
            generated = provider.generate_mcqs(current_request)
            if provider_result is None:
                provider_result = generated.provider_result.model_copy(deep=True)
            else:
                provider_result.latency_ms += generated.provider_result.latency_ms
            validated = self._validate_generated_questions(
                generated,
                sections,
                adaptation_context,
                seen_fingerprints=seen,
            )
            added_by_section = self._merge_needed_questions(combined, validated.questions, expected_by_section)
            warnings.extend(validated.provider_result.warnings)

            missing = self._missing_question_counts(combined, expected_by_section)
            if not missing:
                break
            if attempt < max_attempts:
                warnings.append(
                    "Provider returned short output; requested repair for "
                    + ", ".join(f"section {section_id}: {count}" for section_id, count in missing.items())
                )
                repair_sections = [contexts_by_id[section_id] for section_id in missing]
                current_request = GenerationRequest(
                    sections=repair_sections,
                    questions_per_section=max(missing.values()),
                    adaptation_context=AdaptationContext(
                        prior_session_count=adaptation_context.prior_session_count,
                        weak_topics=[
                            item
                            for item in adaptation_context.weak_topics
                            if int(item.get("section_id", 0)) in missing
                        ],
                        avoid_fingerprints=[*adaptation_context.avoid_fingerprints, *seen],
                    ),
                )
            elif not added_by_section:
                break

        missing = self._missing_question_counts(combined, expected_by_section)
        if missing or len(combined) != total_expected:
            raise ValueError(
                f"Provider returned {len(combined)} of {total_expected} requested questions; "
                f"expected exactly {total_expected} questions "
                f"({request.questions_per_section} per selected section). Try a smaller count or another provider."
            )
        if provider_result is None:
            raise ValueError("Provider did not return a question set")
        provider_result.warnings = [*provider_result.warnings, *warnings]
        return GeneratedQuestionSet(questions=combined, provider_result=provider_result)

    def _merge_needed_questions(
        self,
        combined: list[MCQ],
        candidates: list[MCQ],
        expected_by_section: dict[int, int],
    ) -> dict[int, int]:
        """Append only the still-needed questions for each section."""
        added: dict[int, int] = {}
        current_counts = {section_id: 0 for section_id in expected_by_section}
        for question in combined:
            current_counts[question.section_id] += 1
        for question in candidates:
            needed = expected_by_section[question.section_id] - current_counts[question.section_id]
            if needed <= 0:
                continue
            combined.append(question)
            current_counts[question.section_id] += 1
            added[question.section_id] = added.get(question.section_id, 0) + 1
        return added

    def _missing_question_counts(self, questions: list[MCQ], expected_by_section: dict[int, int]) -> dict[int, int]:
        """Return per-section deficits against the requested MCQ counts."""
        counts = {section_id: 0 for section_id in expected_by_section}
        for question in questions:
            if question.section_id in counts:
                counts[question.section_id] += 1
        return {
            section_id: expected - counts[section_id]
            for section_id, expected in expected_by_section.items()
            if counts[section_id] < expected
        }

    def _validate_generated_questions(
        self,
        generated: GeneratedQuestionSet,
        sections: list[Section],
        adaptation_context: AdaptationContext,
        *,
        seen_fingerprints: set[str] | None = None,
    ) -> GeneratedQuestionSet:
        selected = {section.canonical_id for section in sections}
        avoid = set(adaptation_context.avoid_fingerprints)
        seen = seen_fingerprints if seen_fingerprints is not None else set()
        warnings = list(generated.provider_result.warnings)
        for index, question in enumerate(generated.questions, start=1):
            if question.section_id not in selected:
                raise ValueError(f"Generated question references unselected section {question.section_id}")
            if not question.topic.strip():
                raise ValueError("Generated question is missing a topic")
            if not question.explanation.strip():
                raise ValueError("Generated question is missing an explanation")
            fingerprint = question_fingerprint(question)
            if fingerprint in avoid or fingerprint in seen:
                question.question = f"{question.question} Variant {index}."
                fingerprint = question_fingerprint(question)
                warnings.append(f"Adjusted repeated question variant for section {question.section_id}")
            question.fingerprint = fingerprint
            seen.add(fingerprint)
        generated.provider_result.warnings = warnings
        return generated

    def _wrong_label(self, correct: str) -> str:
        labels = ["A", "B", "C", "D"]
        index = labels.index(correct)
        return labels[(index + 1) % len(labels)]
