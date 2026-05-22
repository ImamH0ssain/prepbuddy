# REST API

Run:

```bash
prepbuddy api --host 127.0.0.1 --port 8000
```

OpenAPI docs are served at `http://127.0.0.1:8000/docs`.

## Typical Flow

```bash
curl -X POST http://127.0.0.1:8000/documents/ingest \
  -H "Content-Type: application/json" \
  -d '{"pdf_path":"SLATEFALL_DOSSIER.pdf"}'

curl http://127.0.0.1:8000/documents

curl http://127.0.0.1:8000/documents/1/sections

curl http://127.0.0.1:8000/documents/1/mapping

curl -X POST http://127.0.0.1:8000/sessions \
  -H "Content-Type: application/json" \
  -d '{"document_id":1,"sections":["5","8"],"questions_per_section":2,"llm":"fake"}'
```

Upload a PDF through multipart form data:

```bash
curl -X POST http://127.0.0.1:8000/documents/upload \
  -F "file=@SLATEFALL_DOSSIER.pdf;type=application/pdf"
```

Submit answers with question IDs returned by `/sessions`:

```bash
curl -X POST http://127.0.0.1:8000/sessions/<session_id>/answers \
  -H "Content-Type: application/json" \
  -d '{"answers":{"<question_id>":"A"}}'
```

Manage stored documents and sessions:

```bash
curl http://127.0.0.1:8000/documents/1/sessions
curl http://127.0.0.1:8000/documents/1/kb/snapshot?limit=5
curl -X DELETE http://127.0.0.1:8000/sessions/<session_id>
curl -X DELETE http://127.0.0.1:8000/documents/1
```

## Error Handling

The API returns:

- `400` for invalid section IDs, ambiguous aliases, malformed answers, and completed-session resubmission.
- `404` for missing sessions or missing ingestion state.
- `503` for unavailable LLM providers.
