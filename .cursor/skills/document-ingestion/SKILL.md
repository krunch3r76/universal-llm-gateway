---
name: document-ingestion
description: Use for scanned PDFs, images, and mixed-format dropbox files under /data/files/. Canonical output is sidecar markdown beside source with YAML frontmatter.
trigger_match_terms: ["document-ingestion", "document_ingestion", "scanned", "pdf", "image", ".extracted.md", "sidecar", "ocr_uri", ".ocr.md", "tooling-observability", "mixed-format", "document", "under", "data"]
---

# Document ingestion via MCP

Use for scanned PDFs, images, and mixed-format dropbox files under `/data/files/`. Canonical output = sidecar markdown beside source: `<basename>[.pages-<spec>][.args-<6hex>].extracted.md` with YAML frontmatter.

## OCR companion on `document:` entities

When a `document:` points at an image/PDF binary, readable text may live in a **companion** — not a second document entity (v0).

| Field | Role |
|---|---|
| `source_uri` | Canonical binary |
| `attributes.ocr_uri` | Readable companion (markdown/plain) |
| Filename default | `{same_stem}.ocr.md` beside the binary under cortex |

**Discovery order:** (1) `entity_get(intent=card)` → `status_summary.ocr_uri` · (2) full entity `attributes.ocr_uri` · (3) derive `{stem}.ocr.md` next to `source_uri` and `fs` probe · (4) assertion `evidence_uris`.

Convention SoT: `cortex://notes/system/specs/document-ocr-companion-convention.md`. After extraction/promote, set `attributes.ocr_uri` when a readable companion exists. Auto-OCR pipeline redesign is out of scope here — this skill owns discovery + promote wiring.

## Decision tree

| Document type | Tool | Why |
|---|---|---|
| text-layer/digital PDF | `fs(op="read")` | fast `pymupdf4llm`, no LLM |
| scanned PDF / image / unsure | `dispatch(tool="extract_document")` | OCR + persistent sidecar |
| batch directory of scans | `dispatch(tool="extract_directory")` | relay batch OCR |
| scanned financial statement needing JSON | `private_dispatch(tool="extract_document_structured")` | finance schema via vision |
| text-layer financial PDF | `finance(op="inspect")` | pdfplumber classification preview |
| reviewed sidecar → evidence bundle | `dispatch(tool="promote_document_to_evidence")` | entity + evidence bundle + FS move |

## Quick workflow

1. Try cheap path for likely digital PDF: `fs(cortex, read, documents/contract.pdf)`.
2. If empty/garbled/scanned, run `extract_document`.
3. Read `sidecar_path` via `fs`.
4. Review, then `promote_document_to_evidence`.
5. Assert with `chunk_id=NULL` when citing promoted extraction.

## Dispatch surface

All agents use `dispatch(tool="...", arguments='{...}')` except private tools.

| Tool | Surface | Purpose |
|---|---|---|
| `extract_document` | `dispatch` | primary OCR/extract + sidecar write |
| `extract_directory` | `dispatch` | batch OCR directory; cost warning |
| `extract_document_structured` | `private_dispatch` | finance JSON extraction from scans |
| `promote_document_to_evidence` | `dispatch` | sidecar → evidence bundle + document entity |

## `extract_document`

Reads file under `/data/files/`, detects format, extracts text, validates frontmatter against `cortex://configs/schemas/extraction-sidecar-v1.yaml`, and atomically writes sidecar. It does **not** create entities.

```python
dispatch(tool="extract_document", arguments='{
  "path": "dropbox/cortex_legal/2026-05-19/bill.pdf",
  "dpi": 0,
  "pages": [2, 3, 4],
  "prompt": "",
  "model": ""
}')
```

| Arg | Default | Notes |
|---|---|---|
| `path` | required | relative to `/data/files/` |
| `dpi` | profile default 200 | `0` = pinned profile |
| `pages` | all | 1-based; partial extractions get `.pages-` infix |
| `prompt` | profile default | hashes into `prompt_hash` / args infix |
| `model` | profile default | Stargate-routable vision model |

`same_source_sha ∧ same_pages ∧ same_args ⇒ return_existing_sidecar`. Source changed ⇒ auto-replace. Collision ⇒ loud `ValueError`.

| `pages=` | Behavior |
|---|---|
| unset / all | Digital PDFs with a text layer stay on `text_pdf` (pymupdf); scanned whole-PDF → vision OCR |
| non-empty list | **Per-page gate:** selected pages whose text is thin/empty **and** that embed images route through `ocr_pages` even when the PDF overall has a text layer; text-rich selected pages stay on pymupdf. Does not force full-PDF OCR. |

Formats: `.pdf`, images, `.docx`, `.odt`, `.eml`, `.html`, `.txt`, `.md`.

## Other tools

```python
dispatch(tool="extract_directory", arguments='{
  "directory": "dropbox/legal_docs/",
  "prompt": "Extract all text faithfully.",
  "dpi": 200,
  "model": ""
}')
```

Prefer per-file `extract_document` when you need durable sidecars with frontmatter.

```python
private_dispatch(tool="extract_document_structured", arguments='{
  "path": "dropbox/cortex_finance/2026-04-20/statement.pdf",
  "statement_type": "checking"
}')
```

Finance routing: text-layer statements ⇒ `finance(op="inspect")` then `finance(op="ingest")`; scanned statements ⇒ `extract_document_structured` then `finance(op="ingest")`.

## `fs` hints

`fs(read)` on scanned PDF/image may return `_next` pointing to `extract_document`. For raw image bytes/vision attachments, use `fs(..., binary=True)`, not `extract_document`.

## Observability internals

Helpers: `services/mcp-server/tools/_extract_document_helpers.py`; handler: `services/mcp-server/tools/extract_document.py`. Events: `mcp.document.extract.{called,idempotent,empty,completed}`, `mcp.document.extract.structured.{called,completed,error}`, `mcp.document.ocr.directory.*`.
