# Case-Law Source Acquisition

Supporting reference for `research-article-ingest`. The Content-Integrity and
Anti-Transcription invariants in the parent SKILL.md apply identically here.
Sibling skill: `legal-opinion-corpus-ingestion`.

## Source Format Priority

**Invariant**: ∀ case-law download: exhaust non-HTML formats before accepting HTML.
PDF is preferred — `fs md_list`/`md_read` work on PDFs via `pymupdf4llm` (structured
section access, no tag-stripping). HTML requires grep-based extraction; fallback only.

Try in sequence, stop at first success:

1. **CourtListener Harvard PDF** — `https://storage.courtlistener.com/harvard_pdf/<opinion_id>.pdf`
   - Find `opinion_id` via `https://www.courtlistener.com/?q="<case name>"&type=o`
   - Covers most published state appellate + Supreme Court opinions; no auth.
   ```bash
   scripts/ingest-article --url https://storage.courtlistener.com/harvard_pdf/<id>.pdf \
       --subdir <subdir> --filename <case-slug>.pdf \
       --title "<Case Name (year) citation>" --date <YYYY-MM-DD>
   ```
2. **CourtListener other PDF** — check opinion page for a PDF link (`storage.courtlistener.com/pdf/...`); same pattern.
3. **Official court PDF** — California Courts, federal PACER, or publisher PDF if directly linkable without auth.
4. **SCOCal HTML** (CA Supreme Court only) — Stanford archive, static HTML, reliable for pre-2000 opinions.
   ```bash
   scripts/ingest-article --url https://scocal.stanford.edu/opinion/<slug> \
       --subdir <subdir> --filename <case-slug>.html \
       --title "<Case Name (year) citation>" --date <YYYY-MM-DD>
   ```
5. **CourtListener HTML** — requires JS; use cursor-ide-browser MCP (`browser_navigate` →
   evaluate `document.body.innerText`) → write to `.html`.
6. **User-prompt fallback** — all automated paths fail (CAPTCHA/auth/JS): see protocol below.

**Never** substitute Justia or vLex HTML for the primary source — not authoritative.
CourtListener and SCOCal are. If the authoritative source is inaccessible, surface the blockage.

## Reading HTML Court Opinions

`fs(op="read")` on `.html` returns raw markup — it does NOT strip tags. `md_list`
finds sections only if the HTML uses real `<h1>`–`<h6>` (most court sites use
`<strong>`/`<p>`, yielding a single `[Preamble]` section). So:

```python
fs(sandbox="workspaces", op="read",
   path="universal-llm-gateway/docs/research/<subdir>/<slug>.html")
Grep(pattern="<target text>", path="docs/research/<subdir>/<slug>.html")
```

SCOCal opinion body is inside `<div id="opinion">…</div>`. Pin-cite page markers
appear as `<strong>[N Cal.Xd NNN]</strong>` inline. Extract paragraphs from that div
for Key Quotations with pin cites.

## User-Prompt Fallback Protocol

When no automated path works (JS wall, CAPTCHA, auth, 403 everywhere), prompt the user with:
the exact source URL, the exact destination path, and what to do after (re-run ingestion or notify).

> I cannot download `<URL>` automatically (requires browser authentication).
> Please download this file and save it to: `docs/research/<subdir>/<filename>`
> Once placed, I will read it and complete the corpus entry.

## Content-Integrity — motivating failure (thread 947, 2026-05-12)

CourtListener `harvard_pdf/5452512` was ingested as `mcdonald-v-antelope-valley-2008.pdf`.
The PDF parsed cleanly to 24,246 chars — but the text was *Matter of Tinker*, a 1926 NY
opinion. Every format check passed (200 OK, valid PDF, text layer, `md_list` sections);
the wrong opinion sat undetected until a pinpoint query returned nonsense. This is why the
two-token identity check in the parent SKILL.md is mandatory before registering any artifact.
