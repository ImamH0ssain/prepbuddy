from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from prepbuddy.api import create_app
from prepbuddy.ingestion import parse_sections_from_pages
from prepbuddy.providers import FakeProvider
from prepbuddy.repository import PrepRepository
from prepbuddy.service import PrepService
from prepbuddy.settings import Settings


def test_api_session_flow(tmp_path: Path) -> None:
    settings = Settings(db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}", llm_provider="fake")
    repo = PrepRepository(settings.db_url)
    repo.save_document(
        path=Path("fixture.pdf"),
        title="Fixture",
        page_count=2,
        content_hash="fixture",
        sections=parse_sections_from_pages([(1, ["Section 1. Alpha", "alpha text"])]),
    )
    app = create_app(PrepService(settings=settings, repository=repo, provider=FakeProvider()))
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    sections = client.get("/sections")
    assert sections.status_code == 200
    assert sections.json()[0]["canonical_id"] == 1

    created = client.post("/sessions", json={"sections": ["1"], "questions_per_section": 1, "llm": "fake"})
    assert created.status_code == 200
    question = created.json()["questions"][0]

    scored = client.post(f"/sessions/{created.json()['session_id']}/answers", json={"answers": {question["id"]: "A"}})
    assert scored.status_code == 200
    assert scored.json()["total"] == 1


def test_api_document_upload_sessions_and_delete_flow(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}",
        data_dir=tmp_path / "data",
        docs_dir=tmp_path / "docs",
        outputs_dir=tmp_path / "outputs",
        llm_provider="fake",
    )

    def fake_parse_pdf(path: Path):
        return "Uploaded", 1, "uploaded-hash", parse_sections_from_pages(
            [(1, ["Section 1. Uploaded", "details for uploaded."])]
        )

    monkeypatch.setattr("prepbuddy.service.parse_pdf", fake_parse_pdf)
    app = create_app(PrepService(settings=settings, provider=FakeProvider()))
    client = TestClient(app)

    uploaded = client.post(
        "/documents/upload",
        files={"file": ("uploaded.pdf", b"pdf bytes", "application/pdf")},
    )
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document_id"]

    documents = client.get("/documents")
    assert documents.status_code == 200
    assert documents.json()[0]["id"] == document_id
    assert documents.json()[0]["original_filename"] == "uploaded.pdf"

    sections = client.get(f"/documents/{document_id}/sections")
    assert sections.status_code == 200
    assert sections.json()[0]["title"] == "Uploaded"

    created = client.post(
        "/sessions",
        json={"document_id": document_id, "sections": ["1"], "questions_per_section": 1, "llm": "fake"},
    )
    assert created.status_code == 200
    assert created.json()["document_id"] == document_id

    sessions = client.get(f"/documents/{document_id}/sessions")
    assert sessions.status_code == 200
    assert sessions.json()[0]["id"] == created.json()["session_id"]

    deleted_session = client.delete(f"/sessions/{created.json()['session_id']}")
    assert deleted_session.status_code == 200
    assert client.get(f"/documents/{document_id}/sessions").json() == []

    created = client.post(
        "/sessions",
        json={"document_id": document_id, "sections": ["1"], "questions_per_section": 1, "llm": "fake"},
    )
    assert created.status_code == 200

    deleted_document = client.delete(f"/documents/{document_id}")
    assert deleted_document.status_code == 200
    assert client.get("/documents").json() == []
    archived_sessions = client.get(f"/documents/{document_id}/sessions")
    assert archived_sessions.status_code == 200
    assert archived_sessions.json()[0]["id"] == created.json()["session_id"]
