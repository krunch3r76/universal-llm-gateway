<!-- target:* -->
# Vision Extensions

**Project vision extensions — domain list, RAG indexing.**

## Domain List

Valid domains for this project:
`pipeline | routing | federation | tooling | inference | rag | measurement`

## RAG Indexing

```bash
curl -sf -X POST http://localhost:8100/index \
  -H 'Content-Type: application/json' \
  -d '{"path": "ABSOLUTE_PATH_TO_VISION_FILE"}'
```

If the RAG service is down, note and move on. Files on disk are ground truth.
<!-- /target:* -->
