"""Command-line interface for PrepBuddy."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import httpx
import typer
from rich.console import Console
from rich.table import Table

from .config_file import update_gemini_key
from .providers import ProviderError
from .service import PrepService
from .settings import Settings
from .ui_helpers import kb_snapshot_tables

app = typer.Typer(help="Adaptive PDF preparation system.")
config_app = typer.Typer(help="Configuration helpers.")
app.add_typer(config_app, name="config")
console = Console(width=160)


def _settings(db: str | None = None) -> Settings:
    settings = Settings()
    if db:
        settings.db_url = db
    settings.ensure_dirs()
    return settings


def _service(db: str | None = None) -> PrepService:
    return PrepService(settings=_settings(db))


def _parse_sections(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _resolve_document_id(service: PrepService, value: str | None) -> int | None:
    if value in {None, "all"}:
        return None
    if value == "latest":
        return service.repository.latest_document_id()
    return int(value)


def _print_snapshot(snapshot) -> None:  # type: ignore[no-untyped-def]
    tables = kb_snapshot_tables(snapshot)
    sessions_table = Table(title="Recent Sessions")
    for column in ["Document", "Status", "Sections", "Provider", "Score", "Completed"]:
        sessions_table.add_column(column)
    for row in tables["sessions"]:
        sessions_table.add_row(
            str(row["document"]),
            str(row["status"]),
            str(row["sections"]),
            str(row["provider"]),
            str(row["score"]),
            str(row["completed"]),
        )
    console.print(sessions_table)
    missed_table = Table(title="Missed Questions")
    for column in ["Session", "Question", "Section", "Topic", "Selected", "Correct", "Clarification"]:
        missed_table.add_column(column)
    for row in tables["missed_questions"]:
        missed_table.add_row(
            str(row["session"]),
            str(row["question"]),
            str(row["section"]),
            str(row["topic"]),
            str(row["selected"]),
            str(row["correct"]),
            str(row["clarification"]),
        )
    console.print(missed_table)


@app.command()
def doctor(paths: Annotated[bool, typer.Option("--paths", help="Show runtime paths.")] = False) -> None:
    """Check local dependencies and provider availability."""
    settings = Settings()
    table = Table(title="PrepBuddy Doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_row("Python", sys.version.split()[0])
    table.add_row("PyMuPDF", "ok" if importlib.util.find_spec("fitz") else "missing")
    table.add_row("FastAPI", "ok" if importlib.util.find_spec("fastapi") else "missing")
    table.add_row("SQLite", sqlite3.sqlite_version)
    table.add_row("GEMINI_API_KEY", "set" if settings.gemini_api_key else "not set")
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=2)
        ollama_status = "reachable" if response.status_code == 200 else f"HTTP {response.status_code}"
        if settings.ollama_model not in response.text:
            ollama_status += f"; model {settings.ollama_model} not listed"
    except httpx.HTTPError as exc:
        ollama_status = f"unreachable: {exc.__class__.__name__}"
    table.add_row("Ollama", ollama_status)
    table.add_row("Docker", shutil.which("docker") or "not found")
    if paths:
        table.add_row("Project root", str(Path.cwd()))
        table.add_row("DB URL", settings.db_url)
        table.add_row("Uploads", str(settings.data_dir / "uploads"))
        table.add_row("Data", str(settings.data_dir))
        table.add_row("Outputs", str(settings.outputs_dir))
    console.print(table)


@app.command()
def ingest(
    pdf: Annotated[Path, typer.Option("--pdf", help="PDF to ingest.")] = Path("SLATEFALL_DOSSIER.pdf"),
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Parse and store document sections plus mapping artifacts."""
    service = _service(db)
    document_id = service.ingest_pdf(pdf)
    console.print(f"Ingested document {document_id} from {pdf}")
    console.print(f"Wrote {service.settings.data_dir / 'section_mapping.json'}")
    console.print(f"Wrote {service.settings.docs_dir / 'section_mapping.md'}")


@app.command("sections")
def list_sections(
    document: Annotated[str, typer.Option("--document", help="latest or a document ID.")] = "latest",
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """List canonical IDs, source labels, titles, pages, aliases, and chunk counts."""
    service = _service(db)
    document_id = _resolve_document_id(service, document)
    table = Table(title="Sections")
    for column in ["ID", "Source", "Title", "Pages", "Chunks", "Aliases"]:
        table.add_column(column)
    for section in service.list_sections(document_id=document_id):
        table.add_row(
            str(section.canonical_id),
            section.source_label,
            section.title,
            f"{section.page_start}-{section.page_end}",
            str(section.chunk_count),
            ", ".join(section.aliases),
        )
    console.print(table)


@app.command("documents")
def documents(db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None) -> None:
    """List all ingested PDF documents."""
    table = Table(title="Documents")
    for column in ["ID", "Title", "Original File", "Pages", "Sections", "Sessions", "Stored Path"]:
        table.add_column(column)
    for document in _service(db).list_documents():
        table.add_row(
            str(document.id),
            document.title,
            document.original_filename or "",
            str(document.page_count),
            str(document.section_count),
            str(document.session_count),
            document.stored_path or document.path,
        )
    console.print(table)


@app.command("sessions")
def sessions(
    document: Annotated[str, typer.Option("--document", help="latest, all, or a document ID.")] = "all",
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """List prep sessions."""
    service = _service(db)
    document_id = _resolve_document_id(service, document)
    table = Table(title="Sessions")
    table.add_column("ID")
    for column in ["Document", "Status", "Sections", "Provider", "Score", "Created", "Completed"]:
        table.add_column(column, no_wrap=True)
    for session in service.list_sessions(document_id=document_id):
        score = "" if session.score is None or session.total is None else f"{session.score}/{session.total}"
        table.add_row(
            session.id,
            str(session.document_id or ""),
            session.status,
            ",".join(str(item) for item in session.sections),
            session.provider,
            score,
            session.created_at.isoformat(),
            session.completed_at.isoformat() if session.completed_at else "",
        )
    console.print(table)


@app.command("delete-session")
def delete_session(
    session_id: str,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion.")] = False,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Delete one prep session."""
    if not yes and not typer.confirm(f"Delete session {session_id}?"):
        raise typer.Exit(1)
    _service(db).delete_session(session_id)
    console.print(f"Deleted session {session_id}")


@app.command("delete-document")
def delete_document(
    document_id: int,
    yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion.")] = False,
    keep_file: Annotated[bool, typer.Option("--keep-file", help="Keep managed upload PDF on disk.")] = False,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Archive one PDF document while preserving its sessions and history."""
    if not yes and not typer.confirm(f"Archive document {document_id}? Sessions and history will be preserved."):
        raise typer.Exit(1)
    _service(db).delete_document(document_id, delete_file=not keep_file)
    console.print(f"Archived document {document_id}")


@app.command("delete-all-documents")
def delete_all_documents(
    yes: Annotated[bool, typer.Option("--yes", help="Confirm archive operation.")] = False,
    keep_files: Annotated[bool, typer.Option("--keep-files", help="Keep managed upload PDFs on disk.")] = False,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Archive every active PDF document while preserving sessions."""
    if not yes and not typer.confirm("Archive all active documents? Sessions and history will be preserved."):
        raise typer.Exit(1)
    _service(db).delete_all_documents(delete_file=not keep_files)
    console.print("Archived all active documents")


@app.command("delete-all-sessions")
def delete_all_sessions(
    yes: Annotated[bool, typer.Option("--yes", help="Confirm deletion.")] = False,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Delete every prep session and derived adaptive records."""
    if not yes and not typer.confirm("Delete all prep sessions?"):
        raise typer.Exit(1)
    _service(db).delete_all_sessions()
    console.print("Deleted all sessions")


@app.command("clear-knowledge-base")
def clear_knowledge_base(
    yes: Annotated[bool, typer.Option("--yes", help="Confirm reset.")] = False,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Reset adaptive learning data while preserving documents and sessions."""
    if not yes and not typer.confirm("Clear adaptive knowledge while preserving documents and sessions?"):
        raise typer.Exit(1)
    _service(db).clear_knowledge_base()
    console.print("Cleared adaptive knowledge base")


@app.command("clear-everything")
def clear_everything(
    yes: Annotated[bool, typer.Option("--yes", help="Confirm destructive wipe.")] = False,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Hard-delete all PrepBuddy records and managed upload files."""
    if not yes and not typer.confirm("Hard-delete all PrepBuddy data and managed uploads?"):
        raise typer.Exit(1)
    _service(db).clear_everything()
    console.print("Cleared all PrepBuddy data")


@app.command("kb-snapshot")
def kb_snapshot(
    document: Annotated[str, typer.Option("--document", help="latest, all, or a document ID.")] = "latest",
    limit: Annotated[int, typer.Option("--limit", min=1)] = 5,
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Print a human-readable KB snapshot."""
    service = _service(db)
    document_id = _resolve_document_id(service, document)
    _print_snapshot(service.repository.snapshot(limit=limit, document_id=document_id))


@app.command("map-sections")
def map_sections(
    document: Annotated[str, typer.Option("--document", help="latest or a document ID.")] = "latest",
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Print the current scenario-to-source section mapping."""
    service = _service(db)
    console.print(json.dumps(service.mapping_payload(document_id=_resolve_document_id(service, document)), indent=2))


@app.command()
def prep(
    sections: Annotated[str, typer.Option("--sections", help="Comma-separated section IDs, labels, or aliases.")],
    questions_per_section: Annotated[int, typer.Option("--questions-per-section", min=1)] = 5,
    answers_mode: Annotated[str, typer.Option("--answers-mode", help="interactive or simulate")] = "interactive",
    llm: Annotated[str, typer.Option("--llm", help="auto, gemini, ollama, or fake")] = "auto",
    document: Annotated[str, typer.Option("--document", help="latest or a document ID.")] = "latest",
    db: Annotated[str | None, typer.Option("--db", help="SQLAlchemy DB URL.")] = None,
) -> None:
    """Run one prep session."""
    service = _service(db)
    document_id = _resolve_document_id(service, document)
    selected = _parse_sections(sections)
    try:
        generated = service.create_session(
            selected,
            questions_per_section=questions_per_section,
            provider_name=llm,
            document_id=document_id,
        )
        if answers_mode == "simulate":
            answers = service.simulate_answers(generated.questions)
        elif answers_mode == "interactive":
            answers = {}
            for question in generated.questions:
                number = question.question_number or 0
                console.print(f"\nQ{number} [Section {question.section_id}] {question.question}")
                for choice in question.choices:
                    console.print(f"  {choice.label}. {choice.text}")
                answers[question.id or ""] = typer.prompt("Answer", default="A").strip().upper()
        else:
            raise typer.BadParameter("answers-mode must be interactive or simulate")
        result = service.submit_answers(generated.session_id, answers)
    except ProviderError as exc:
        raise typer.Exit(f"Provider error: {exc}") from exc
    except ValueError as exc:
        raise typer.Exit(f"Error: {exc}") from exc
    console.print(f"Session {result.session_id}: {result.score}/{result.total}")
    for item in result.results:
        if not item.is_correct:
            number = item.question_number or "?"
            console.print(f"Question {number}: correct={item.correct_answer}; {item.clarification}")


@app.command("scenario-a")
def scenario_a(
    sections: Annotated[str, typer.Option("--sections")] = "3,7",
    out: Annotated[Path, typer.Option("--out")] = Path("outputs/scenario_a"),
    questions_per_section: Annotated[int, typer.Option("--questions-per-section", min=1)] = 5,
    llm: Annotated[str, typer.Option("--llm")] = "auto",
    document: Annotated[str, typer.Option("--document")] = "latest",
    db: Annotated[str | None, typer.Option("--db")] = None,
) -> None:
    """Run and export Scenario A."""
    service = _service(db)
    result = service.run_scenario_a(
        sections=_parse_sections(sections),
        output_root=out,
        questions_per_section=questions_per_section,
        provider_name=llm,
        document_id=_resolve_document_id(service, document),
    )
    console.print(f"Wrote Scenario A to {out}; score {result.score}/{result.total}")


@app.command("scenario-b")
def scenario_b(
    out: Annotated[Path, typer.Option("--out")] = Path("outputs"),
    questions_per_section: Annotated[int, typer.Option("--questions-per-section", min=1)] = 5,
    llm: Annotated[str, typer.Option("--llm")] = "auto",
    document: Annotated[str, typer.Option("--document")] = "latest",
    db: Annotated[str | None, typer.Option("--db")] = None,
) -> None:
    """Run and export required Scenario B iterations."""
    try:
        service = _service(db)
        results = service.run_scenario_b(
            output_root=out,
            questions_per_section=questions_per_section,
            provider_name=llm,
            document_id=_resolve_document_id(service, document),
        )
    except (ProviderError, ValueError) as exc:
        raise typer.Exit(f"Error: {exc}") from exc
    for index, result in enumerate(results, start=1):
        console.print(f"Iteration {index}: {result.score}/{result.total}")
    console.print(f"Wrote Scenario B outputs under {out}")


@app.command("export-kb")
def export_kb(
    out: Annotated[Path, typer.Option("--out")] = Path("kb_snapshot.json"),
    limit: Annotated[int, typer.Option("--limit", min=1)] = 5,
    document: Annotated[str, typer.Option("--document")] = "all",
    format: Annotated[str, typer.Option("--format", help="json or markdown")] = "json",
    db: Annotated[str | None, typer.Option("--db")] = None,
) -> None:
    """Export a human-readable KB snapshot."""
    service = _service(db)
    snapshot = service.repository.snapshot(limit=limit, document_id=_resolve_document_id(service, document))
    out.parent.mkdir(parents=True, exist_ok=True)
    if format == "json":
        out.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    elif format == "markdown":
        tables = kb_snapshot_tables(snapshot)
        lines = ["# KB Snapshot", "", "## Recent Sessions", ""]
        for row in tables["sessions"]:
            lines.append(f"- {row['document']}: {row['score']} ({row['sections']})")
        lines.extend(["", "## Missed Questions", ""])
        for row in tables["missed_questions"]:
            lines.append(f"- {row['question']} section {row['section']}: {row['clarification']}")
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        raise typer.BadParameter("format must be json or markdown")
    console.print(f"Wrote {out}")


@config_app.command("set-gemini-key")
def set_gemini_key(
    key: Annotated[str, typer.Option("--key", prompt=True, hide_input=True)],
    env_path: Annotated[Path, typer.Option("--env-path")] = Path(".env"),
) -> None:
    """Persist a Gemini API key into the project .env file."""
    update_gemini_key(env_path, key)
    console.print(f"Updated {env_path}")


@app.command()
def api(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8000,
) -> None:
    """Run the REST API."""
    import uvicorn

    uvicorn.run("prepbuddy.api:app", host=host, port=port, reload=False)


@app.command()
def ui(
    host: Annotated[str, typer.Option("--host")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port")] = 8501,
    open_browser: Annotated[bool, typer.Option("--open-browser/--no-open-browser")] = True,
) -> None:
    """Run the Streamlit UI."""
    ui_path = Path(__file__).with_name("ui.py")
    console.print("Starting PrepBuddy UI. If a browser does not open, use the Local URL printed by Streamlit.")
    raise_code = subprocess.call(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            f"--server.headless={str(not open_browser).lower()}",
            f"--server.address={host}",
            f"--server.port={port}",
            "--browser.gatherUsageStats=false",
            str(ui_path),
        ]
    )
    if raise_code:
        raise typer.Exit(raise_code)


if __name__ == "__main__":
    app()
