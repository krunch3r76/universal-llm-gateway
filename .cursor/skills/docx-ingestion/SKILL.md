---
trigger_match_terms: ["docx-ingestion", "docx_ingestion", ".docx", "DOCX", "word document", "extract_document", "python-docx"]
description: On any `.docx` file. Read for the MCP ingest path, RAG indexing semantics, and Cortex ingest differences vs PDF.
---

# DOCX ingestion

Use for any `.docx` file needing MCP extraction, sidecar review, or RAG indexing.

## Route

`DOCX ⇒ dispatch(tool="extract_document") → .extracted.md sidecar → fs(read sidecar)`.

```python
dispatch(tool="extract_document", arguments='{"path": "dropbox/my-doc.docx"}')
```

`extract_document` opens `.docx` under `/data/files/`, classifies it as rich text, extracts with `python-docx`, and writes a YAML-frontmatter `.extracted.md` sidecar beside the source. Sidecar naming follows `document-ingestion`.

Read result:

```python
fs(sandbox="cortex", op="read", path="<sidecar_path>")
```

Primary IDE tool lists may omit dispatch targets; still call through `dispatch(tool="extract_document", ...)`.

## Staging

Use `fs` to stage DOCX bytes when needed (`write_binary` in cortex sandbox) and to read generated sidecars.

## RAG rule

`rag` does **not** natively index raw DOCX. It indexes markdown/code/PDF/EPUB/HTML/plain text. For DOCX: extract first, then index the extracted markdown sidecar.

## Related

Load `document-ingestion` for full sidecar naming, promotion, and evidence-bundle workflow.
