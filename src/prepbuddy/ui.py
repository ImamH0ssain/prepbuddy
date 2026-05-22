"""Streamlit UI for multi-document PrepBuddy sessions."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from prepbuddy.config_file import gemini_key_status, update_gemini_key
from prepbuddy.providers import ProviderError
from prepbuddy.schemas import GeneratedSession, SessionResult
from prepbuddy.service import PrepService
from prepbuddy.settings import Settings
from prepbuddy.ui_helpers import (
    format_answer_feedback,
    format_question_heading,
    kb_snapshot_tables,
    section_option_label,
)


NAV_ITEMS = ["Start", "Session", "Knowledge"]
GLOBAL_DANGER_ACTIONS = {
    "Delete all documents": "Archive every active document. Sessions and history are preserved.",
    "Delete all sessions": "Delete all sessions and derived adaptive records. Documents remain available.",
    "Clear knowledge base": "Reset adaptive memory while preserving documents and visible sessions.",
    "Clear everything": "Hard-delete all PrepBuddy documents, sessions, history, and managed uploads.",
}


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
    """Load a persisted session into Streamlit state."""
    session = service.repository.get_generated_session(session_id)
    st.session_state["active_session_id"] = session_id
    st.session_state["active_document_id"] = session.document_id
    st.session_state["generated_session"] = session.model_dump(mode="json")
    st.session_state["force_active_tab"] = "Session"
    st.session_state.pop("last_result", None)


def _inject_danger_styles() -> None:
    """Style primary buttons as red destructive confirmations."""
    st.markdown(
        """
        <style>
        div[data-testid="stButton"] button[kind="primary"] {
            background-color: #b42318;
            border-color: #b42318;
            color: #ffffff;
        }
        div[data-testid="stButton"] button[kind="primary"]:hover {
            background-color: #912018;
            border-color: #912018;
            color: #ffffff;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _set_pending_action(action: dict[str, Any]) -> None:
    """Store a destructive action that requires the shared confirmation button."""
    st.session_state["pending_danger_action"] = action


def _execute_danger_action(service: PrepService, action: dict[str, Any]) -> None:
    """Run a confirmed destructive action and repair UI state."""
    kind = action["type"]
    if kind == "delete_document":
        document_id = int(action["document_id"])
        service.delete_document(document_id)
        if st.session_state.get("active_document_id") == document_id:
            st.session_state.pop("active_document_id", None)
            st.session_state.pop("active_session_id", None)
            st.session_state.pop("generated_session", None)
            st.session_state.pop("last_result", None)
    elif kind == "delete_session":
        session_id = str(action["session_id"])
        service.delete_session(session_id)
        if st.session_state.get("active_session_id") == session_id:
            st.session_state.pop("active_session_id", None)
            st.session_state.pop("generated_session", None)
            st.session_state.pop("last_result", None)
    elif kind == "delete_all_documents":
        service.delete_all_documents()
        for key in ("active_document_id", "active_session_id", "generated_session", "last_result"):
            st.session_state.pop(key, None)
    elif kind == "delete_all_sessions":
        service.delete_all_sessions()
        for key in ("active_session_id", "generated_session", "last_result"):
            st.session_state.pop(key, None)
    elif kind == "clear_knowledge_base":
        service.clear_knowledge_base()
        st.session_state.pop("last_result", None)
    elif kind == "clear_everything":
        service.clear_everything()
        for key in ("active_document_id", "active_session_id", "generated_session", "last_result"):
            st.session_state.pop(key, None)
    else:
        raise ValueError(f"Unknown danger action: {kind}")


def _render_pending_confirmation(service: PrepService) -> None:
    """Render the shared pending-action confirmation controls."""
    pending = st.session_state.get("pending_danger_action")
    if not pending:
        return
    st.sidebar.warning(str(pending["message"]))
    cols = st.sidebar.columns([0.65, 0.35])
    if cols[0].button("Confirm selection", key="confirm_pending_danger", type="primary"):
        try:
            _execute_danger_action(service, pending)
            st.session_state.pop("pending_danger_action", None)
            st.rerun()
        except ValueError as exc:
            st.sidebar.error(str(exc))
    if cols[1].button("Cancel", key="cancel_pending_danger"):
        st.session_state.pop("pending_danger_action", None)
        st.rerun()


def _render_sidebar(service: PrepService) -> int | None:
    """Render document, session, upload, settings, and danger-zone controls."""
    st.sidebar.title("PrepBuddy")
    documents = service.list_documents()

    with st.sidebar.expander("Documents", expanded=True):
        upload = st.file_uploader("Upload PDF document", type=["pdf"])
        if st.button("Upload and ingest", disabled=upload is None):
            if upload is not None:
                try:
                    document_id = service.ingest_uploaded_pdf(upload.name, upload.getvalue())
                    st.session_state["active_document_id"] = document_id
                    st.session_state["force_active_tab"] = "Start"
                    st.session_state.pop("generated_session", None)
                    st.session_state.pop("active_session_id", None)
                    st.success("PDF ingested.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        documents = service.list_documents()
        if not documents:
            st.caption("No uploaded documents yet.")
        for document in documents:
            cols = st.columns([0.72, 0.28])
            if cols[0].button(_document_label(document), key=f"select_document_{document.id}"):
                st.session_state["active_document_id"] = document.id
                st.session_state["force_active_tab"] = "Start"
                st.session_state.pop("active_session_id", None)
                st.session_state.pop("generated_session", None)
                st.session_state.pop("last_result", None)
                st.rerun()
            if cols[1].button("Delete", key=f"archive_document_{document.id}"):
                _set_pending_action(
                    {
                        "type": "delete_document",
                        "document_id": document.id,
                        "message": f"Archive document {document.id}? Sessions and history will be preserved.",
                    }
                )

    if not documents:
        _render_pending_confirmation(service)
        return None

    active_document_id = st.session_state.get("active_document_id")
    if active_document_id not in {document.id for document in documents}:
        active_document_id = documents[-1].id
        st.session_state["active_document_id"] = active_document_id

    with st.sidebar.expander("Sessions", expanded=True):
        sessions = service.list_sessions(document_id=int(active_document_id))
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
                _set_pending_action(
                    {
                        "type": "delete_session",
                        "session_id": session.id,
                        "message": f"Delete session {session.id[:8]}?",
                    }
                )

    with st.sidebar.expander("Settings", expanded=False):
        st.session_state["provider"] = st.selectbox(
            "Provider",
            ["auto", "gemini", "ollama", "fake"],
            index=["auto", "gemini", "ollama", "fake"].index(st.session_state.get("provider", "auto")),
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
        action_label = st.selectbox("Action", list(GLOBAL_DANGER_ACTIONS))
        st.caption(GLOBAL_DANGER_ACTIONS[action_label])
        if st.button("Confirm selection", key="confirm_global_danger", type="primary"):
            action_type = {
                "Delete all documents": "delete_all_documents",
                "Delete all sessions": "delete_all_sessions",
                "Clear knowledge base": "clear_knowledge_base",
                "Clear everything": "clear_everything",
            }[action_label]
            try:
                _execute_danger_action(service, {"type": action_type})
                st.rerun()
            except ValueError as exc:
                st.error(str(exc))

    _render_pending_confirmation(service)
    return int(active_document_id)


def _documents_table(service: PrepService) -> list[dict[str, object]]:
    """Return active document rows for the Start table."""
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


def _select_active_document(service: PrepService, document_id: int) -> int:
    """Render the Start document selector and return the selected ID."""
    documents = service.list_documents()
    labels = {_document_label(document): document.id for document in documents}
    current_label = next(
        (label for label, candidate_id in labels.items() if candidate_id == document_id),
        next(iter(labels)),
    )
    selected_label = st.selectbox("Active document", list(labels), index=list(labels).index(current_label))
    selected_document_id = labels[selected_label]
    if selected_document_id != document_id:
        st.session_state["active_document_id"] = selected_document_id
        st.session_state.pop("active_session_id", None)
        st.session_state.pop("generated_session", None)
        st.session_state.pop("last_result", None)
        st.rerun()
    return selected_document_id


def _render_start_tab(service: PrepService, document_id: int) -> None:
    """Render the document overview and session generation workflow."""
    st.subheader("Documents")
    st.dataframe(_documents_table(service), width="stretch")

    document_id = _select_active_document(service, document_id)
    sections = service.list_sections(document_id=document_id)
    mapping = service.mapping_payload(document_id=document_id)

    st.subheader("Section Mapping")
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
    questions_per_section = st.number_input(
        "Questions per section",
        min_value=1,
        max_value=20,
        value=int(st.session_state.get("questions_per_section", 5)),
    )
    st.session_state["questions_per_section"] = int(questions_per_section)
    if st.button("Generate Session", disabled=not selected_labels):
        try:
            generated = service.create_session(
                [options[label] for label in selected_labels],
                questions_per_section=int(questions_per_section),
                provider_name=str(st.session_state.get("provider", "auto")),
                document_id=document_id,
            )
            st.session_state["active_session_id"] = generated.session_id
            st.session_state["generated_session"] = generated.model_dump(mode="json")
            st.session_state["force_active_tab"] = "Session"
            st.session_state.pop("last_result", None)
            st.rerun()
        except (ProviderError, ValueError) as exc:
            st.error(str(exc))


def _render_score_summary(score: int, total: int) -> None:
    """Render a single score line for generated or completed sessions."""
    st.success(f"Score: {score}/{total}")


def _render_generated_session(service: PrepService, generated: dict[str, object]) -> None:
    """Render active session questions or completed feedback."""
    session = GeneratedSession.model_validate(generated)
    st.caption(f"Status: {session.status} | Provider: {session.provider_result.provider}")

    result_payload = st.session_state.get("last_result")
    result = SessionResult.model_validate(result_payload) if result_payload else None
    if result:
        _render_score_summary(result.score, result.total)
        result_by_number = {item.question_number: item for item in result.results}
        for question in result.questions:
            number = question.question_number or 0
            st.markdown(f"**{format_question_heading(number, question.question)}**")
            for choice in question.choices:
                st.write(f"{choice.label}. {choice.text}")
            item = result_by_number.get(number)
            if item is not None:
                feedback = format_answer_feedback(
                    question_number=number,
                    is_correct=item.is_correct,
                    correct_answer=item.correct_answer,
                    clarification=item.clarification,
                )
                if item.is_correct:
                    st.success(feedback)
                else:
                    st.warning(feedback)
        return

    if session.score is not None and session.total is not None:
        _render_score_summary(session.score, session.total)
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
    """Render readable KB snapshot tables for the active document."""
    snapshot = service.repository.snapshot(limit=10, document_id=document_id)
    tables = kb_snapshot_tables(snapshot)
    st.subheader("Recent Sessions")
    st.dataframe(tables["sessions"], width="stretch")
    st.subheader("Missed Questions")
    st.dataframe(tables["missed_questions"], width="stretch")
    section_ids = [section.canonical_id for section in service.list_sections(document_id=document_id)]
    st.subheader("Weak Topics")
    st.dataframe(service.repository.weak_topics(section_ids, document_id=document_id), width="stretch")


def _active_tab() -> str:
    """Render stateful navigation and return the selected main view."""
    forced = st.session_state.pop("force_active_tab", None)
    if forced in NAV_ITEMS:
        st.session_state["active_tab"] = forced
        st.session_state["active_tab_selector"] = forced
    current = st.session_state.get("active_tab", "Start")
    if current not in NAV_ITEMS:
        current = "Start"
    if st.session_state.get("active_tab_selector") not in NAV_ITEMS:
        st.session_state["active_tab_selector"] = current
    selected = st.segmented_control(
        "View",
        NAV_ITEMS,
        key="active_tab_selector",
        label_visibility="collapsed",
    )
    if selected:
        st.session_state["active_tab"] = str(selected)
    return str(st.session_state.get("active_tab", "Start"))


def main() -> None:
    """Render the Streamlit application."""
    st.set_page_config(page_title="PrepBuddy", layout="wide", initial_sidebar_state="expanded")
    _inject_danger_styles()
    service = _active_service()
    document_id = _render_sidebar(service)

    st.title("PrepBuddy")
    if document_id is None:
        st.info("Upload a PDF from the sidebar to start a preparation library.")
        return

    document = service.repository.get_document(document_id)
    provider = st.session_state.get("provider", "auto")
    st.caption(
        f"Active: {document.title} | {document.page_count} pages | "
        f"{document.section_count} sections | {document.session_count} sessions | provider: {provider}"
    )

    tab = _active_tab()
    if tab == "Start":
        _render_start_tab(service, document_id)
    elif tab == "Session":
        generated = st.session_state.get("generated_session")
        if generated:
            _render_generated_session(service, generated)
        else:
            st.info("Generate or open a session from the sidebar.")
    else:
        _render_knowledge_tab(service, document_id)


if __name__ == "__main__":
    main()
