"""Streamlit UI for multi-document PrepBuddy sessions."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from prepbuddy.config_file import gemini_key_status, update_gemini_key
from prepbuddy.providers import ProviderError
from prepbuddy.schemas import GeneratedSession, SessionResult
from prepbuddy.service import PrepService
from prepbuddy.settings import Settings
from prepbuddy.ui_helpers import format_question_heading, kb_snapshot_tables, section_option_label


@st.cache_resource
def _service(gemini_key_override: str | None = None) -> PrepService:
    """Create a service instance, keyed by the optional UI Gemini override."""
    settings = Settings()
    if gemini_key_override:
        settings.gemini_api_key = gemini_key_override
    return PrepService(settings=settings)


def _active_service() -> PrepService:
    """Return the service matching the current Streamlit settings."""
    key = st.session_state.get("gemini_api_key_override")
    return _service(key if isinstance(key, str) and key else None)


def _document_label(document) -> str:  # type: ignore[no-untyped-def]
    filename = document.original_filename or Path(document.path).name
    return f"{document.id} - {filename}"


def _load_session(service: PrepService, session_id: str) -> None:
    session = service.repository.get_generated_session(session_id)
    st.session_state["active_session_id"] = session_id
    st.session_state["generated_session"] = session.model_dump(mode="json")
    st.session_state.pop("last_result", None)


def _render_sidebar(service: PrepService) -> int | None:
    """Render document, session, upload, settings, and danger-zone controls."""
    st.sidebar.title("PrepBuddy")
    documents = service.list_documents()

    with st.sidebar.expander("Upload PDF", expanded=not documents):
        upload = st.file_uploader("PDF document", type=["pdf"])
        if st.button("Upload and ingest", disabled=upload is None):
            if upload is not None:
                try:
                    document_id = service.ingest_uploaded_pdf(upload.name, upload.getvalue())
                    st.session_state["active_document_id"] = document_id
                    st.session_state.pop("generated_session", None)
                    st.session_state.pop("active_session_id", None)
                    st.success("PDF ingested.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

    if not documents:
        return None

    labels = {_document_label(document): document.id for document in documents}
    active_document_id = st.session_state.get("active_document_id", documents[-1].id)
    current_label = next(
        (label for label, document_id in labels.items() if document_id == active_document_id),
        next(reversed(labels)),
    )
    selected_label = st.sidebar.selectbox("Document", list(labels), index=list(labels).index(current_label))
    selected_document_id = labels[selected_label]
    st.session_state["active_document_id"] = selected_document_id

    with st.sidebar.expander("Sessions", expanded=True):
        sessions = service.list_sessions(document_id=selected_document_id)
        if not sessions:
            st.caption("No sessions for this document.")
        for session in sessions:
            score = "" if session.score is None or session.total is None else f" {session.score}/{session.total}"
            label = f"{session.status}{score} - {session.created_at:%Y-%m-%d %H:%M}"
            cols = st.columns([0.65, 0.35])
            if cols[0].button(label, key=f"open_session_{session.id}"):
                _load_session(service, session.id)
                st.rerun()
            if cols[1].button("Delete", key=f"delete_session_{session.id}"):
                try:
                    service.delete_session(session.id)
                    if st.session_state.get("active_session_id") == session.id:
                        st.session_state.pop("active_session_id", None)
                        st.session_state.pop("generated_session", None)
                        st.session_state.pop("last_result", None)
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))

    with st.sidebar.expander("Settings", expanded=False):
        st.session_state["provider"] = st.selectbox(
            "Provider",
            ["auto", "gemini", "ollama", "fake"],
            index=["auto", "gemini", "ollama", "fake"].index(st.session_state.get("provider", "auto")),
        )
        st.session_state["questions_per_section"] = st.number_input(
            "Questions per section",
            min_value=1,
            max_value=20,
            value=int(st.session_state.get("questions_per_section", 5)),
        )
        session_key = st.text_input("Gemini API key", type="password", value="")
        if session_key:
            st.session_state["gemini_api_key_override"] = session_key
            st.info(gemini_key_status(settings_key=service.settings.gemini_api_key, session_key=session_key))
        else:
            st.caption(
                "Gemini key status: "
                + gemini_key_status(
                    settings_key=service.settings.gemini_api_key,
                    session_key=st.session_state.get("gemini_api_key_override"),
                )
            )
        save_key = st.checkbox("Save Gemini key to .env for future CLI/UI runs")
        if st.button("Save Gemini settings"):
            active_key = session_key or st.session_state.get("gemini_api_key_override")
            if not active_key:
                st.warning("Enter a Gemini API key first.")
            elif save_key:
                update_gemini_key(Path(".env"), str(active_key))
                _service.clear()
                st.success("Gemini key saved to .env.")
            else:
                st.session_state["gemini_api_key_override"] = str(active_key)
                _service.clear()
                st.success("Gemini key set for this UI session.")

    with st.sidebar.expander("Danger Zone", expanded=False):
        confirm = st.checkbox("Confirm document deletion")
        if st.button("Delete selected document", disabled=not confirm):
            try:
                service.delete_document(selected_document_id)
                st.session_state.pop("active_document_id", None)
                st.session_state.pop("active_session_id", None)
                st.session_state.pop("generated_session", None)
                st.session_state.pop("last_result", None)
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    return selected_document_id


def _documents_table(service: PrepService) -> list[dict[str, object]]:
    return [
        {
            "id": document.id,
            "title": document.title,
            "original_filename": document.original_filename,
            "pages": document.page_count,
            "sections": document.section_count,
            "sessions": document.session_count,
            "stored_path": document.stored_path,
            "windows_path": document.windows_path,
            "wsl_path": document.wsl_path,
            "created_at": document.created_at,
        }
        for document in service.list_documents()
    ]


def _render_prep_tab(service: PrepService, document_id: int) -> None:
    sections = service.list_sections(document_id=document_id)
    mapping = service.mapping_payload(document_id=document_id)
    st.dataframe(mapping, width="stretch")
    options = {
        section_option_label(
            canonical_id=section.canonical_id,
            title=section.title,
            source_label=section.source_label,
            page_start=section.page_start,
            page_end=section.page_end,
        ): str(section.canonical_id)
        for section in sections
    }
    selected_labels = st.multiselect("Sections", list(options), default=list(options)[:1])
    if st.button("Generate Session", disabled=not selected_labels):
        try:
            generated = service.create_session(
                [options[label] for label in selected_labels],
                questions_per_section=int(st.session_state.get("questions_per_section", 5)),
                provider_name=str(st.session_state.get("provider", "auto")),
                document_id=document_id,
            )
            st.session_state["active_session_id"] = generated.session_id
            st.session_state["generated_session"] = generated.model_dump(mode="json")
            st.session_state.pop("last_result", None)
            st.rerun()
        except (ProviderError, ValueError) as exc:
            st.error(str(exc))


def _render_generated_session(service: PrepService, generated: dict[str, object]) -> None:
    session = GeneratedSession.model_validate(generated)
    st.caption(f"Status: {session.status} | Provider: {session.provider_result.provider}")
    if session.score is not None and session.total is not None:
        st.success(f"Score: {session.score}/{session.total}")

    result_payload = st.session_state.get("last_result")
    result = SessionResult.model_validate(result_payload) if result_payload else None
    if result:
        st.success(f"Score: {result.score}/{result.total}")
        wrong_by_number = {item.question_number: item for item in result.results if not item.is_correct}
        for question in result.questions:
            number = question.question_number or 0
            st.markdown(f"**{format_question_heading(number, question.question)}**")
            for choice in question.choices:
                st.write(f"{choice.label}. {choice.text}")
            if number in wrong_by_number:
                item = wrong_by_number[number]
                st.warning(f"Question {number}: correct answer {item.correct_answer}. {item.clarification}")
        return

    if session.status == "completed":
        st.info("This completed session is stored in the Knowledge tab.")
        return

    with st.form(f"answers_{session.session_id}"):
        answers: dict[str, str] = {}
        for question in session.questions:
            number = question.question_number or 0
            st.markdown(f"**{format_question_heading(number, question.question)}**")
            labels = [choice.label for choice in question.choices]
            captions = {choice.label: choice.text for choice in question.choices}
            selected = st.radio(
                f"Answer for Question {number}",
                labels,
                format_func=lambda label: f"{label}. {captions[label]}",
                key=f"answer_{session.session_id}_{question.id}",
            )
            if question.id:
                answers[question.id] = selected
        if st.form_submit_button("Submit Answers"):
            try:
                result = service.submit_answers(session.session_id, answers)
                st.session_state["last_result"] = result.model_dump(mode="json")
                st.session_state["generated_session"] = service.repository.get_generated_session(
                    session.session_id
                ).model_dump(mode="json")
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))


def _render_knowledge_tab(service: PrepService, document_id: int) -> None:
    snapshot = service.repository.snapshot(limit=10, document_id=document_id)
    tables = kb_snapshot_tables(snapshot)
    st.subheader("Recent Sessions")
    st.dataframe(tables["sessions"], width="stretch")
    st.subheader("Missed Questions")
    st.dataframe(tables["missed_questions"], width="stretch")
    section_ids = [section.canonical_id for section in service.list_sections(document_id=document_id)]
    st.subheader("Weak Topics")
    st.dataframe(service.repository.weak_topics(section_ids, document_id=document_id), width="stretch")


def main() -> None:
    """Render the Streamlit application."""
    st.set_page_config(page_title="PrepBuddy", layout="wide", initial_sidebar_state="expanded")
    service = _active_service()
    document_id = _render_sidebar(service)

    st.title("PrepBuddy")
    if document_id is None:
        st.info("Upload a PDF from the sidebar to start a preparation library.")
        return

    document = service.repository.get_document(document_id)
    st.caption(
        f"{document.title} | {document.page_count} pages | "
        f"{document.section_count} sections | {document.session_count} sessions"
    )
    documents_tab, prep_tab, session_tab, knowledge_tab = st.tabs(["Documents", "Prep", "Session", "Knowledge"])
    with documents_tab:
        st.dataframe(_documents_table(service), width="stretch")
    with prep_tab:
        _render_prep_tab(service, document_id)
    with session_tab:
        generated = st.session_state.get("generated_session")
        if generated:
            _render_generated_session(service, generated)
        else:
            st.info("Generate or open a session from the sidebar.")
    with knowledge_tab:
        _render_knowledge_tab(service, document_id)


if __name__ == "__main__":
    main()

