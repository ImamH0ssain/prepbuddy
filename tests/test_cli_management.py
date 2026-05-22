from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from prepbuddy.cli import app
from prepbuddy.ingestion import parse_sections_from_pages
from prepbuddy.providers import FakeProvider
from prepbuddy.repository import PrepRepository
from prepbuddy.schemas import AdaptationContext, AnswerChoice, MCQ, ProviderResult


runner = CliRunner()


def _seed_repo(db_url: str) -> tuple[PrepRepository, int, str]:
    repo = PrepRepository(db_url)
    document_id = repo.save_document(
        path=Path("fixture.pdf"),
        title="Fixture",
        page_count=1,
        content_hash="fixture-hash",
        sections=parse_sections_from_pages([(1, ["Section 1. Fixture", "details for fixture."])]),
    )
    section = repo.find_section_by_token("1", document_id=document_id)
    generated = repo.create_generated_session(
        sections=[section],
        questions=[
            MCQ(
                section_id=1,
                topic="Fixture topic",
                question="Fixture question?",
                choices=[
                    AnswerChoice(label="A", text="Correct"),
                    AnswerChoice(label="B", text="Wrong"),
                    AnswerChoice(label="C", text="Wrong"),
                    AnswerChoice(label="D", text="Wrong"),
                ],
                correct_answer="A",
                explanation="The fixture says so.",
            )
        ],
        provider_result=ProviderResult(provider=FakeProvider.name, model=FakeProvider.model),
        adaptation_context=AdaptationContext(),
    )
    return repo, document_id, generated.session_id


def test_cli_documents_sessions_and_kb_snapshot(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'prep.sqlite'}"
    _seed_repo(db_url)

    documents = runner.invoke(app, ["documents", "--db", db_url])
    assert documents.exit_code == 0
    assert "Fixture" in documents.output

    sessions = runner.invoke(app, ["sessions", "--document", "latest", "--db", db_url])
    assert sessions.exit_code == 0
    assert "generated" in sessions.output

    snapshot = runner.invoke(app, ["kb-snapshot", "--document", "latest", "--db", db_url])
    assert snapshot.exit_code == 0
    assert "Recent Sessions" in snapshot.output


def test_cli_delete_session_and_document(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'prep.sqlite'}"
    repo, document_id, session_id = _seed_repo(db_url)

    deleted_session = runner.invoke(app, ["delete-session", session_id, "--yes", "--db", db_url])
    assert deleted_session.exit_code == 0
    assert repo.list_sessions(document_id=document_id) == []

    deleted_document = runner.invoke(app, ["delete-document", str(document_id), "--yes", "--keep-file", "--db", db_url])
    assert deleted_document.exit_code == 0
    assert repo.list_documents() == []

