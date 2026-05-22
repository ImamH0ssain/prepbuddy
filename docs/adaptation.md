# Adaptive Generation

PrepBuddy distinguishes cold-start and returning runs.

## Cold Start

If selected sections have no completed history, the prompt uses balanced section context and asks for the configured number of MCQs per section.

## Returning Runs

For later runs, the service queries only the active document's history:

- completed sessions involving the selected canonical section IDs for that document;
- topic-level wrong/correct counts;
- recent question fingerprints to avoid repetition.

The LLM request receives:

- `prior_session_count`;
- `weak_topics`, sorted by wrong count and attempts;
- `avoid_fingerprints`.

The provider is instructed to focus first on weak topics and avoid repeated questions. After scoring, `topic_stats` and a top-5 KB snapshot are updated.

`clear-knowledge-base` deletes adaptive aggregates and records a reset timestamp. Older sessions remain visible for review, but prior-session counts and repetition fingerprints ignore sessions completed before the reset.

## Scenario B

Scenario B demonstrates adaptation:

- iteration 1: sections `5,8`;
- iteration 2: sections `6,8,9`;
- iteration 3: section `8`.

Because section `8` appears in all three iterations, iteration 3 has prior weak-topic context from iterations 1 and 2.

If several PDFs are ingested, scenario commands should be run with `--document <id>` or `--document latest` so the adaptive history and section mapping come from the intended document.
