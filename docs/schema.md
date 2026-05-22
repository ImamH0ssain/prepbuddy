# Knowledge Base Schema

PrepBuddy uses SQLite through SQLAlchemy 2.x. The schema is intentionally small and local-first.

## Document Tables

- `documents`: one row per ingested PDF, including original filename, managed stored path, source path, content hash, and upload/deletion metadata.
- `sections`: top-level sections with canonical ID, source label, title, text, and page range.
- `section_aliases`: normalized lookup aliases for canonical IDs, labels, and titles.
- `section_chunks`: section text chunks used as LLM context.

## Session Tables

- `prep_sessions`: generated/completed session metadata, document ID, provider, score, and adaptation context.
- `session_sections`: selected sections for each session.
- `questions`: persisted generated MCQs with topic, fingerprint, answer, and explanation.
- `answer_choices`: choices A-D for each question.
- `answers`: submitted answer, correctness, and clarification.

## Adaptive Tables

- `topic_stats`: aggregated attempts/correct/wrong counts by section and topic.
- `kb_snapshots`: JSON snapshots of recent completed sessions; UI and CLI render these as readable tables.
- `generation_events`: provider/model/latency/token/warning metadata.
- `app_state`: application-wide maintenance markers such as the knowledge-base reset timestamp.

Document deletion archives rows by setting deletion metadata; hard deletion is reserved for the explicit clear-everything operation.
