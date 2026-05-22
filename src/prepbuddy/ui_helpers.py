"""Pure formatting helpers for the Streamlit UI."""

from __future__ import annotations

from .schemas import KBSnapshot


def section_option_label(
    *,
    canonical_id: int,
    title: str,
    source_label: str,
    page_start: int,
    page_end: int,
) -> str:
    """Return the user-facing label for a selectable section."""
    return f"{canonical_id} - {title} ({source_label}, pages {page_start}-{page_end})"


def format_question_heading(question_number: int, question: str) -> str:
    """Return a user-facing MCQ heading without exposing internal IDs."""
    return f"Question {question_number}. {question}"


def kb_snapshot_tables(snapshot: KBSnapshot) -> dict[str, list[dict[str, object]]]:
    """Convert a KB snapshot into readable table rows for Streamlit."""
    sessions: list[dict[str, object]] = []
    missed_questions: list[dict[str, object]] = []
    for session in snapshot.sessions:
        sections = session.get("sections", [])
        score = session.get("score")
        total = session.get("total")
        sessions.append(
            {
                "document": session.get("document_title") or session.get("document_id") or "Unknown",
                "status": session.get("status", "completed"),
                "sections": ", ".join(str(item) for item in sections),
                "provider": session.get("provider", ""),
                "score": "" if score is None or total is None else f"{score}/{total}",
                "completed": session.get("completed_at") or "",
            }
        )
        for question in session.get("questions", []):
            if not isinstance(question, dict) or question.get("is_correct") is not False:
                continue
            number = question.get("question_number") or "?"
            missed_questions.append(
                {
                    "session": str(session.get("session_id", ""))[:8],
                    "question": f"Question {number}",
                    "section": question.get("section_id"),
                    "topic": question.get("topic"),
                    "selected": question.get("selected_answer"),
                    "correct": question.get("correct_answer"),
                    "clarification": question.get("clarification", ""),
                }
            )
    return {"sessions": sessions, "missed_questions": missed_questions}

