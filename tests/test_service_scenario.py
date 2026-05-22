from __future__ import annotations

import json
from pathlib import Path

from prepbuddy.ingestion import parse_sections_from_pages
from prepbuddy.providers import FakeProvider
from prepbuddy.repository import PrepRepository
from prepbuddy.schemas import AdaptationContext, AnswerChoice, GeneratedQuestionSet, GenerationRequest, MCQ, ProviderResult
from prepbuddy.service import PrepService
from prepbuddy.settings import Settings


def _seed_document(repo: PrepRepository) -> int:
    pages = []
    for idx in range(1, 11):
        pages.append((idx, [f"Section {idx}. Topic {idx}", f"Important details for topic {idx}."]))
    return repo.save_document(
        path=Path("fixture.pdf"),
        title="Fixture",
        page_count=10,
        content_hash="fixture",
        sections=parse_sections_from_pages(pages),
    )


def test_scenario_b_exports_required_files_and_adapts_iteration_three(tmp_path: Path) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}",
        data_dir=tmp_path / "data",
        docs_dir=tmp_path / "docs",
        outputs_dir=tmp_path / "outputs",
        llm_provider="fake",
    )
    repo = PrepRepository(settings.db_url)
    _seed_document(repo)
    service = PrepService(settings=settings, repository=repo, provider=FakeProvider())

    service.run_scenario_b(output_root=tmp_path / "outputs", questions_per_section=2)

    iter3_questions = tmp_path / "outputs" / "scenario_b_iter3" / "questions_iter3.json"
    iter3_snapshot = tmp_path / "outputs" / "scenario_b_iter3" / "kb_snapshot_iter3.json"
    assert iter3_questions.exists()
    assert iter3_snapshot.exists()

    payload = json.loads(iter3_questions.read_text(encoding="utf-8"))
    assert payload["session"]["sections"] == [8]
    assert payload["session"]["adaptation_context"]["weak_topics"]
    assert payload["session"]["adaptation_context"]["prior_session_count"] >= 2
    assert payload["session"]["score"] < payload["session"]["total"]


def test_submit_answers_rejects_completed_session(tmp_path: Path) -> None:
    settings = Settings(db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}", llm_provider="fake")
    repo = PrepRepository(settings.db_url)
    _seed_document(repo)
    service = PrepService(settings=settings, repository=repo, provider=FakeProvider())

    generated = service.create_session(["1"], questions_per_section=1, provider_name="fake")
    answers = {generated.questions[0].id: generated.questions[0].correct_answer}
    service.submit_answers(generated.session_id, answers)

    try:
        service.submit_answers(generated.session_id, answers)
    except ValueError as exc:
        assert "already completed" in str(exc)
    else:
        raise AssertionError("Expected completed session rejection")


def test_submit_answers_aggregates_duplicate_topics_in_one_session(tmp_path: Path) -> None:
    settings = Settings(db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}", llm_provider="fake")
    repo = PrepRepository(settings.db_url)
    _seed_document(repo)
    section = repo.find_section_by_token("1")
    choices = [
        AnswerChoice(label="A", text="Correct"),
        AnswerChoice(label="B", text="Wrong"),
        AnswerChoice(label="C", text="Wrong"),
        AnswerChoice(label="D", text="Wrong"),
    ]
    generated = repo.create_generated_session(
        sections=[section],
        questions=[
            MCQ(
                section_id=1,
                topic="Registry and Designations",
                question="Question 1?",
                choices=choices,
                correct_answer="A",
                explanation="Explanation 1",
            ),
            MCQ(
                section_id=1,
                topic="Registry and Designations",
                question="Question 2?",
                choices=choices,
                correct_answer="A",
                explanation="Explanation 2",
            ),
        ],
        provider_result=ProviderResult(provider="fake", model="duplicate-topic-test"),
        adaptation_context=AdaptationContext(),
    )

    result = repo.complete_session(
        generated.session_id,
        {question.id: "A" for question in generated.questions if question.id},
    )
    weak_topics = repo.weak_topics([1])

    assert result.score == 2
    assert weak_topics == []


def _generated_question(section_id: int, number: int) -> MCQ:
    return MCQ(
        section_id=section_id,
        topic=f"topic-{section_id}-{number}",
        question=f"Question {section_id}-{number}?",
        choices=[
            AnswerChoice(label="A", text="Correct"),
            AnswerChoice(label="B", text="Wrong"),
            AnswerChoice(label="C", text="Wrong"),
            AnswerChoice(label="D", text="Wrong"),
        ],
        correct_answer="A",
        explanation="The section supports the correct answer.",
    )


class ShortThenRepairProvider:
    name = "short-repair"
    model = "short-repair-model"

    def __init__(self) -> None:
        self.requests: list[GenerationRequest] = []

    def generate_mcqs(self, request: GenerationRequest) -> GeneratedQuestionSet:
        self.requests.append(request)
        questions: list[MCQ] = []
        per_section = 3 if len(self.requests) == 1 else request.questions_per_section
        for section in request.sections:
            for number in range(1, per_section + 1):
                offset = 0 if len(self.requests) == 1 else 100
                questions.append(_generated_question(section.canonical_id, number + offset))
        return GeneratedQuestionSet(
            questions=questions,
            provider_result=ProviderResult(provider=self.name, model=self.model),
        )


class AlwaysShortProvider:
    name = "always-short"
    model = "always-short-model"

    def generate_mcqs(self, request: GenerationRequest) -> GeneratedQuestionSet:
        return GeneratedQuestionSet(
            questions=[_generated_question(section.canonical_id, 1) for section in request.sections],
            provider_result=ProviderResult(provider=self.name, model=self.model),
        )


def test_create_session_repairs_short_provider_output_to_exact_requested_count(tmp_path: Path) -> None:
    settings = Settings(db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}", llm_provider="fake")
    repo = PrepRepository(settings.db_url)
    _seed_document(repo)
    provider = ShortThenRepairProvider()
    service = PrepService(settings=settings, repository=repo, provider=provider)

    generated = service.create_session(["1", "2"], questions_per_section=5, provider_name="auto")

    assert len(provider.requests) == 2
    assert provider.requests[1].questions_per_section == 2
    assert [question.section_id for question in generated.questions].count(1) == 5
    assert [question.section_id for question in generated.questions].count(2) == 5
    assert len(generated.questions) == 10


def test_create_session_fails_when_provider_cannot_reach_exact_requested_count(tmp_path: Path) -> None:
    settings = Settings(db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}", llm_provider="fake")
    repo = PrepRepository(settings.db_url)
    _seed_document(repo)
    service = PrepService(settings=settings, repository=repo, provider=AlwaysShortProvider())

    try:
        service.create_session(["1", "2"], questions_per_section=5, provider_name="auto")
    except ValueError as exc:
        assert "exactly 10 questions" in str(exc)
    else:
        raise AssertionError("Expected exact-count validation failure")
