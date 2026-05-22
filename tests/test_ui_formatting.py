from __future__ import annotations

from datetime import datetime, timezone

from prepbuddy.schemas import KBSnapshot
from prepbuddy.ui_helpers import format_question_heading, kb_snapshot_tables, section_option_label


def test_ui_labels_hide_internal_ids_and_use_visible_numbers() -> None:
    assert format_question_heading(2, "What is documented?") == "Question 2. What is documented?"
    assert "uuid" not in format_question_heading(2, "What is documented?")
    assert section_option_label(
        canonical_id=5,
        title="Operational Tactics",
        source_label="Section 5",
        page_start=10,
        page_end=12,
    ) == "5 - Operational Tactics (Section 5, pages 10-12)"


def test_kb_snapshot_tables_are_human_readable() -> None:
    snapshot = KBSnapshot(
        generated_at=datetime(2026, 5, 20, tzinfo=timezone.utc),
        sessions=[
            {
                "session_id": "session-123",
                "document_id": 7,
                "document_title": "Dossier",
                "created_at": "2026-05-20T00:00:00+00:00",
                "completed_at": "2026-05-20T00:01:00+00:00",
                "sections": [5],
                "score": 0,
                "total": 1,
                "provider": "fake",
                "questions": [
                    {
                        "question_number": 1,
                        "section_id": 5,
                        "topic": "Tactics",
                        "question": "Question?",
                        "correct_answer": "A",
                        "selected_answer": "B",
                        "is_correct": False,
                        "clarification": "Review the tactics section.",
                    }
                ],
            }
        ],
    )

    tables = kb_snapshot_tables(snapshot)

    assert tables["sessions"][0]["document"] == "Dossier"
    assert tables["missed_questions"][0]["question"] == "Question 1"
    assert tables["missed_questions"][0]["clarification"] == "Review the tactics section."

