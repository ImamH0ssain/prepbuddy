"""FastAPI application for PrepBuddy."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from .providers import ProviderError
from .service import PrepService


class IngestRequest(BaseModel):
    """Request body for document ingestion."""

    pdf_path: str | None = None


class SessionCreateRequest(BaseModel):
    """Request body for MCQ session creation."""

    document_id: int | None = None
    sections: list[str] = Field(..., examples=[["5", "8"]])
    questions_per_section: int = Field(default=5, ge=1, le=20)
    llm: str = Field(default="auto", examples=["auto", "gemini", "ollama", "fake"])


class AnswerSubmitRequest(BaseModel):
    """Request body for answer submission."""

    answers: dict[str, str]


def create_app(service: PrepService | None = None) -> FastAPI:
    """Create the FastAPI app with an injectable service for tests."""
    prep_service = service or PrepService()
    app = FastAPI(
        title="PrepBuddy Adaptive Document Preparation API",
        version="0.1.0",
        description="Backend-driven PDF prep sessions with adaptive MCQ generation.",
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Return service health."""
        return {"status": "ok"}

    @app.post("/documents/ingest")
    def ingest(request: IngestRequest) -> dict[str, int]:
        """Ingest a PDF and persist its section mapping."""
        try:
            document_id = prep_service.ingest_pdf(Path(request.pdf_path) if request.pdf_path else None)
            return {"document_id": document_id}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/documents")
    def documents() -> list[dict[str, object]]:
        """List all ingested PDFs."""
        return [document.model_dump(mode="json") for document in prep_service.list_documents()]

    @app.post("/documents/upload")
    def upload_document(file: UploadFile = File(...)) -> dict[str, int]:
        """Upload, store, and ingest a PDF document."""
        if not file.filename or not file.filename.lower().endswith(".pdf"):
            raise HTTPException(status_code=400, detail="Only PDF uploads are supported")
        try:
            content = file.file.read()
            document_id = prep_service.ingest_uploaded_pdf(file.filename, content)
            return {"document_id": document_id}
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/documents/{document_id}")
    def delete_document(document_id: int, delete_file: bool = Query(default=True)) -> dict[str, str]:
        """Archive an ingested document while preserving prep history."""
        try:
            prep_service.delete_document(document_id, delete_file=delete_file)
            return {"status": "archived"}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/documents")
    def delete_all_documents(delete_file: bool = Query(default=True)) -> dict[str, str]:
        """Archive all active documents while preserving prep history."""
        prep_service.delete_all_documents(delete_file=delete_file)
        return {"status": "archived"}

    @app.get("/sections")
    def sections() -> list[dict[str, object]]:
        """List stored sections."""
        try:
            return [section.model_dump(mode="json") for section in prep_service.list_sections()]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/documents/{document_id}/sections")
    def document_sections(document_id: int) -> list[dict[str, object]]:
        """List sections for one document."""
        try:
            return [section.model_dump(mode="json") for section in prep_service.list_sections(document_id=document_id)]
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/sections/mapping")
    def mapping() -> list[dict[str, object]]:
        """Return canonical-to-source section mappings."""
        try:
            return prep_service.mapping_payload()
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/documents/{document_id}/mapping")
    def document_mapping(document_id: int) -> list[dict[str, object]]:
        """Return canonical-to-source section mappings for one document."""
        try:
            return prep_service.mapping_payload(document_id=document_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/sessions")
    def create_session(request: SessionCreateRequest) -> dict[str, object]:
        """Generate a new unanswered prep session."""
        try:
            return prep_service.create_session(
                request.sections,
                questions_per_section=request.questions_per_section,
                provider_name=request.llm,
                document_id=request.document_id,
            ).model_dump(mode="json")
        except ProviderError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/documents/{document_id}/sessions")
    def document_sessions(document_id: int) -> list[dict[str, object]]:
        """List sessions for one document."""
        return [session.model_dump(mode="json") for session in prep_service.list_sessions(document_id=document_id)]

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, str]:
        """Delete one prep session."""
        try:
            prep_service.delete_session(session_id)
            return {"status": "deleted"}
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.delete("/sessions")
    def delete_all_sessions() -> dict[str, str]:
        """Delete every prep session and derived adaptive records."""
        prep_service.delete_all_sessions()
        return {"status": "deleted"}

    @app.post("/sessions/{session_id}/answers")
    def submit_answers(session_id: str, request: AnswerSubmitRequest) -> dict[str, object]:
        """Submit answers and return a scored session."""
        try:
            return prep_service.submit_answers(session_id, request.answers).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, object]:
        """Return a generated or completed session."""
        try:
            return prep_service.repository.get_generated_session(session_id).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/history")
    def history(section_ids: str = Query(..., description="Comma-separated canonical section IDs")) -> dict[str, object]:
        """Return adaptive history signals for selected sections."""
        try:
            ids = [int(item.strip()) for item in section_ids.split(",") if item.strip()]
            return {
                "section_ids": ids,
                "prior_session_count": prep_service.repository.prior_session_count(ids),
                "weak_topics": prep_service.repository.weak_topics(ids),
                "avoid_fingerprints": prep_service.repository.recent_fingerprints(ids),
            }
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/kb/snapshot")
    def snapshot(limit: int = Query(default=5, ge=1, le=25)) -> dict[str, object]:
        """Return a human-readable KB snapshot."""
        return prep_service.repository.snapshot(limit=limit).model_dump(mode="json")

    @app.delete("/kb")
    def clear_knowledge_base() -> dict[str, str]:
        """Reset adaptive knowledge while preserving documents and sessions."""
        prep_service.clear_knowledge_base()
        return {"status": "cleared"}

    @app.delete("/maintenance/everything")
    def clear_everything() -> dict[str, str]:
        """Hard-delete every PrepBuddy record and managed upload."""
        prep_service.clear_everything()
        return {"status": "cleared"}

    @app.get("/documents/{document_id}/kb/snapshot")
    def document_snapshot(document_id: int, limit: int = Query(default=5, ge=1, le=25)) -> dict[str, object]:
        """Return a document-scoped KB snapshot."""
        return prep_service.repository.snapshot(limit=limit, document_id=document_id).model_dump(mode="json")

    return app


app = create_app()
