from __future__ import annotations

import hashlib
from pathlib import Path

from prepbuddy.ingestion import parse_sections_from_pages
from prepbuddy.providers import FakeProvider
from prepbuddy.repository import PrepRepository
from prepbuddy.service import PrepService
from prepbuddy.settings import Settings


def _sections(label: str):
    return parse_sections_from_pages([(1, ["Section 1. " + label, f"details for {label.lower()}."])])


def test_repository_lists_documents_with_counts_and_paths(tmp_path: Path) -> None:
    repo = PrepRepository(f"sqlite:///{tmp_path / 'prep.sqlite'}")
    first_id = repo.save_document(
        path=Path("alpha.pdf"),
        title="Alpha",
        page_count=1,
        content_hash="alpha-hash",
        sections=_sections("Alpha"),
        original_filename="alpha.pdf",
        stored_path=Path("data/uploads/alpha.pdf"),
        source_path=Path("alpha.pdf"),
        is_managed_upload=True,
    )
    second_id = repo.save_document(
        path=Path("beta.pdf"),
        title="Beta",
        page_count=1,
        content_hash="beta-hash",
        sections=_sections("Beta"),
        original_filename="beta.pdf",
        stored_path=Path("data/uploads/beta.pdf"),
        source_path=Path("beta.pdf"),
        is_managed_upload=True,
    )

    documents = repo.list_documents()

    assert [document.id for document in documents] == [first_id, second_id]
    assert documents[0].section_count == 1
    assert documents[0].session_count == 0
    assert documents[0].original_filename == "alpha.pdf"
    assert documents[0].stored_path.endswith("data/uploads/alpha.pdf")
    assert documents[0].windows_path
    assert documents[0].wsl_path


def test_sessions_are_scoped_to_selected_document(tmp_path: Path) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}",
        data_dir=tmp_path / "data",
        docs_dir=tmp_path / "docs",
        outputs_dir=tmp_path / "outputs",
        llm_provider="fake",
    )
    repo = PrepRepository(settings.db_url)
    first_id = repo.save_document(
        path=Path("alpha.pdf"),
        title="Alpha",
        page_count=1,
        content_hash="alpha-hash",
        sections=_sections("Alpha"),
    )
    second_id = repo.save_document(
        path=Path("beta.pdf"),
        title="Beta",
        page_count=1,
        content_hash="beta-hash",
        sections=_sections("Beta"),
    )
    service = PrepService(settings=settings, repository=repo, provider=FakeProvider())

    generated = service.create_session(["1"], questions_per_section=1, provider_name="fake", document_id=second_id)

    assert generated.document_id == second_id
    assert repo.list_sessions(document_id=first_id) == []
    assert repo.list_sessions(document_id=second_id)[0].id == generated.session_id
    assert generated.questions[0].question_number == 1


def test_delete_session_and_document_remove_dependent_rows_and_managed_file(tmp_path: Path) -> None:
    uploads_dir = tmp_path / "data" / "uploads"
    uploads_dir.mkdir(parents=True)
    stored_pdf = uploads_dir / "managed.pdf"
    stored_pdf.write_bytes(b"%PDF managed")
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}",
        data_dir=tmp_path / "data",
        docs_dir=tmp_path / "docs",
        outputs_dir=tmp_path / "outputs",
        llm_provider="fake",
    )
    repo = PrepRepository(settings.db_url)
    document_id = repo.save_document(
        path=stored_pdf,
        title="Managed",
        page_count=1,
        content_hash="managed-hash",
        sections=_sections("Managed"),
        original_filename="managed.pdf",
        stored_path=stored_pdf,
        source_path=stored_pdf,
        is_managed_upload=True,
    )
    service = PrepService(settings=settings, repository=repo, provider=FakeProvider())
    generated = service.create_session(["1"], questions_per_section=1, provider_name="fake", document_id=document_id)

    repo.delete_session(generated.session_id)

    assert repo.list_sessions(document_id=document_id) == []

    repo.delete_document(document_id, uploads_dir=uploads_dir)

    assert repo.list_documents() == []
    assert not stored_pdf.exists()


def test_ingest_uploaded_pdf_stores_managed_file_and_reuses_duplicate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    settings = Settings(
        db_url=f"sqlite:///{tmp_path / 'prep.sqlite'}",
        data_dir=tmp_path / "data",
        docs_dir=tmp_path / "docs",
        outputs_dir=tmp_path / "outputs",
        llm_provider="fake",
    )
    service = PrepService(settings=settings)
    content = b"same pdf bytes"
    content_hash = hashlib.sha256(content).hexdigest()

    def fake_parse_pdf(path: Path):
        return "Uploaded", 1, content_hash, _sections("Uploaded")

    monkeypatch.setattr("prepbuddy.service.parse_pdf", fake_parse_pdf)

    first_id = service.ingest_uploaded_pdf("My Brief.pdf", content)
    second_id = service.ingest_uploaded_pdf("Other Name.pdf", content)

    document = service.repository.get_document(first_id)
    assert first_id == second_id
    assert document.is_managed_upload is True
    assert document.original_filename == "My Brief.pdf"
    assert Path(document.stored_path or "").exists()
    assert len(list((settings.data_dir / "uploads").glob("*.pdf"))) == 1
    assert (settings.data_dir / "mappings" / f"document_{first_id}.json").exists()
    assert (settings.docs_dir / "mappings" / f"document_{first_id}.md").exists()
