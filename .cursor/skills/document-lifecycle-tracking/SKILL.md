---
name: document-lifecycle-tracking
description: "On document create, archive, dropbox ingest, upload intake, or locating durable docs tied to cases — session outputs ephemeral, Cortex canonical."
trigger_match_terms: ["document-lifecycle-tracking", "document_lifecycle_tracking", "dropbox", "cortex://dropbox", "uploaded file", "rendered PDF", "artifact inventory", "§13 Artifacts Index", "where is the PDF", "lost the file", "case file artifacts", ".archive", "render_subgraph canvas"]
related_skills: ["matter-playbook-lifecycle", "markdown-navigation", "document-review-timeline-linkage-audit"]
---

# Document Lifecycle Tracking

Apply to any agent creating, archiving, moving, citing, ingesting, or locating durable documents tied to active cases.

Core invariant: session outputs and uploads are ephemeral until copied to the cortex sandbox and cited by durable IDs/URIs. `dropbox/` is staging only.

## Artifact classes

| Class | Definition | Required handling |
|---|---|---|
| Living document | Cross-session markdown tied to active case work | Keep in cortex sandbox; register `document:` entity; never delete while case active |
| Rendered snapshot | PDF/DOCX/HTML/export/canvas derived from a source | Ephemeral unless written to cortex. If durable, add `rendered_from` and index it |
| Archived version | Superseded prior version | Move to `.archive/`; set `archived_on`; do not delete |
| One-time artifact | Call notes, OCR extracts, one-off analysis | Register if case evidence; archive after close |

`session_output_path ∨ /mnt/user-data/uploads/ ⇒ ephemeral_until(copied_to_cortex)`.

## §13 Artifacts Index

Every active case file MUST contain `## §13 Artifacts Index`.

Minimum columns: `Artifact | Type | Path | Status | Entity`.

Rules:
- If Status = `Re-render from .md source`, the `.md` path is canonical; do not report the ephemeral PDF path as canonical.
- New `case:` creation MUST initialize §13 in the linked case file, not retrofit later.
- Case file row + pre-existing living docs are minimum initial rows.
- A case entity without §13 is structurally incomplete; next agent touching it retrofits.

## Document entity convention

Each living/case-evidence artifact gets one `document:` entity:

- ID: `document:<case-prefix>-<artifact-slug>`.
- Required: `source_uri`, `case_entity`, `status`/`lifecycle_status`.
- Rendered snapshot: `rendered_from:<md-source-path>`; `status: ephemeral` unless written to cortex.
- OCR/extracted markdown: separate `document:` entity with `derived_from:<original-path>`.
- Wire relationship from case/account/person to document (`has_artifact`, `evidence_for`, or case convention).

Never register a `document:` entity without `source_uri`.

## Dropbox ingest protocol

`cortex://dropbox/...` in user input ⇒ ingest directive; execute before other work.

Full sequence:
1. List staging path: `fs(sandbox="cortex", op="list", path="dropbox/<subpath>")`.
2. Read each file for content/metadata.
3. Move each file (not copy) to canonical permanent path:
   - financial statements → `documents/finance/...`
   - legal documents → `notes/legal/...`
4. Create one `document:` entity per moved file; `source_uri` points to permanent path.
5. Assert key facts on relevant account/case/person with `evidence_uris` pointing to permanent paths.
6. Wire relationships to each document entity.
7. Verify dropbox path is empty.
8. Search touched durable docs/entities for `dropbox/`; completion bar is zero provenance matches.

Provenance invariant: `dropbox/` MUST NOT appear in entity `source_uri`, assertion `evidence_uris`, or provenance tables inside living documents. Move-without-repoint is not done.

## Uploaded and binary files

User-uploaded files, PDFs, images, signed forms:
1. Identify uploads immediately.
2. If relevant to active case, copy to canonical cortex path (`write_binary` for binary; `write` for text).
3. For PDFs needing retrieval, OCR/extract to `<filename>.extracted.md`.
4. Register binary and extraction as separate `document:` entities; update §13.
5. Never store base64 binary inside assertion `evidence`; cite path/URI.

If one-time input not tied to a case, process in-session without document registration.

## Rendered artifacts and canvases

Rendered PDF/DOCX from a living doc:
- If persistence needed: write to cortex immediately, register document entity with `rendered_from`, and add to §13.
- If only in session output: treat as unrecoverable; note in §13 and re-render from source on demand.

`render_subgraph` canvas convention:
- Path: `notes/<domain>/<case-slug>-canvas-YYYY-MM-DD.md` in cortex.
- Keep dated snapshots; do not delete prior renders.
- Markdown only for standard canvases.
- Entity registration optional unless cited in dispatch/handoff/standing artifact.
- Footer: render timestamp, exact `render_subgraph` call, session ID, and prior canvas superseded.

Do not leave canvases only inline or in `/mnt/user-data/outputs/`.

## Audit checklist

When creating/inventorying a case:
1. List case directory.
2. Classify each file.
3. Ensure every living/case-evidence document has a `document:` entity.
4. Ensure §13 exists and is current.
5. Register durable rendered PDFs/canvases when cited or needed.
6. Move superseded versions to `.archive/` and record `archived_on`.
7. Search durable docs for stale `dropbox/` provenance.

## Relative links in cortex markdown

For cortex-stored markdown, prefer relative links for sibling/parent files that humans open via local markdown readers. Avoid absolute `/data/files/...` paths. `cortex://` URIs remain valid evidence URIs; relative links improve click-through navigation.

Examples: `[Case file](case-file.md)`, `[Exhibit](../../../documents/property/exhibit.extracted.md)`.

## Anti-patterns

- Reporting web-anthropic output or upload paths as canonical.
- Skipping §13 because a case is nearly done.
- Deleting archived versions.
- Copying from dropbox instead of moving.
- Asserting facts with `dropbox/` evidence URIs.
- Moving staged files but leaving provenance tables/assertions/entity attributes pointed at staging.
- Writing canvas/render output only to chat or `/mnt/user-data/outputs/`.
