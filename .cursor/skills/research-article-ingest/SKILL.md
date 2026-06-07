---
name: research-article-ingest
description: Download research papers and register with RAG via scripts/ingest-article — primary-source PDF bytes only, content-integrity check before register.
---

# Research article ingest

**Tool**: `scripts/ingest-article` (host-side CLI — outbound internet access).
**Constraint**: Services in universal-llm-gateway do NOT make outbound connections.
Downloads run on the host; metadata registration goes through the RAG REST API.

## Primary Source vs. Derivative Artifact (HARD RULE)

**Invariant**: ∀ research-article ingestion: the committed artifact is the
raw served bytes from the source — the actual PDF (or HTML page when no PDF
exists). RAG extracts text at index time via `pymupdf4llm.to_markdown()` for
PDFs and the HTML reader for HTML. ¬ hand-curated markdown summaries,
extracted-abstract files, or paraphrased "ingest entries" saved in place of
the source. That is transcription / derivation, not ingestion.

**Agent analysis is NOT a corpus artifact** — ∀ agent-authored content
(Petition Relevance, Holding summaries, argument integration, application
notes, negative-citation warnings): do NOT commit to `docs/research/` and do
NOT register in RAG. The research corpus is primary-source-only.

**Where agent analysis belongs**: Cortex. Seed as assertions on the relevant
case entity (`legal_matter:`, `document:`, or a `case-law:` typed entity).
Cortex search surfaces these at retrieval time alongside the RAG-indexed
source text — the right separation of primary source (RAG) from agent
reasoning (Cortex).

**Default rule** (applies unless overridden by a compelling reason
documented in the task spec or surfaced to the user before acting):

> Save the actual served bytes as-is. arXiv serves PDFs → save the PDF.
> A paper on a publisher page with only an HTML rendering → save the HTML.
> If the source serves the wrong thing (paywall stub, abstract-only,
> "preview" PDF), STOP and inform — do not synthesize a substitute.

**Compelling-reason carve-outs** (rare; surface before acting):

- The served bytes are an interstitial / Cloudflare challenge / login wall →
  use the `curl_cffi` Chrome-impersonation path below, save the
  post-challenge response body
- Multiple authoritative formats exist (e.g. arXiv PDF + publisher PDF) →
  prefer arXiv (stable, versioned, freely citable); document the choice
- The source served a wrong/stub document → STOP and inform; do not save
  the wrong bytes as if they were the source

Companion notes (executive summary, relevance memo, agent-authored analysis)
go in a SEPARATE sibling file clearly labeled as agent commentary — never
the primary entry. Cross-skill: same rule governs
`legal-opinion-corpus-ingestion` for case law and statutes.

## Workflow

### 1. Locate papers

Search the web for relevant papers. Prefer arXiv when available (direct PDF URL).
For each paper, collect: arXiv ID (or URL), title, authors, published date.

### 2. Choose subdirectory and scope

Existing subdirectories under `docs/research/`:

| Subdirectory | RAG scope | Content |
|---|---|---|
| `rag-systems` | `rag_systems` | RAG architecture, evaluation, benchmarks |
| `prompting` | `small_llm_prompting` | Prompt engineering for small/local models |
| `llm/prompting` | `llm_prompting` | Prompt engineering for large/cloud models |
| `workflows` | `workflows` | Pipeline architecture, agent orchestration |
| `code-retrieval` | `code_retrieval` | Code embeddings, AST chunking, dependency retrieval |
| `documentation` | `code_documentation` | Code doc generation, doc-code alignment, summarization |
| `software-agents` | `software_agents` | Agent workflows, tool registries, multi-agent SE |
| `knowledge-management` | `knowledge_systems` | PKM, second brain, agent memory |
| `graph-modeling` | `graph_modeling` | Property graphs, RDF/OWL, KG construction |
| `temporal-provenance` | `temporal_provenance` | Bitemporal, versioning, provenance |
| `belief-consistency` | `belief_consistency` | Belief revision, contradiction handling, ER |
| `information-extraction` | `information_extraction` | NER, relation extraction, structured output |
| `code-transformation` | `code_transformation` | LLM code review, refactoring, hallucination mitigation |

New subdirectories are allowed. If creating one:
1. Create the directory under `docs/research/`
2. Add the subdirectory→scope mapping to `scripts/backfill_article_metadata.py` (`SUBDIRECTORY_TO_SCOPE`)
3. Add a new scope block to `~/.gateway/rag.yaml` under `scopes:`
4. Add the prefix to composite scopes (`all_corpus`, `research`) in `rag.yaml`

### 3. Download and register

```bash
# arXiv paper (most common)
scripts/ingest-article --arxiv <ID> --subdir <subdir> \
    --filename <slug>.pdf \
    --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> \
    --scope <scope>

# Non-arXiv URL
scripts/ingest-article --url <pdf-url> --subdir <subdir> \
    --filename <slug>.pdf \
    --title "<title>" --authors "<authors>" --date <YYYY-MM-DD>

# With immediate indexing (requires RAG running)
scripts/ingest-article --arxiv <ID> --subdir <subdir> ... --index
```

The tool:
1. Downloads the PDF to `docs/research/{subdir}/{filename}`
2. Computes SHA-256 content_hash
3. Calls `POST /api/v1/rag/article` (via Stargate) to upsert the metadata row
4. Optionally calls `POST /api/v1/rag/index` for immediate indexing

If Stargate/RAG is not running, the tool downloads the file and skips registration
gracefully. The metadata can be registered later by re-running the command (it
detects the existing file and skips the download) or via
`scripts/backfill_article_metadata.py` after adding the entry to the YAML registry.

### 4. Verify

**First**: run the Content-Integrity check (see invariant below) — confirm
the downloaded bytes contain the document the metadata claims. Then:

```bash
# Check file exists
ls docs/research/<subdir>/<filename>

# Check article row exists (direct SQLite)
sqlite3 ~/.rag/store/rag_metadata.db \
  "SELECT filename, scope FROM articles WHERE filename = '<filename>'"

# Check indexing (requires RAG running)
# MCP: dispatch(tool="rag_coverage")
```

### 5. YAML registry (optional but recommended)

For batch operations and reproducibility, also add entries to
`~/.rag/article_registry.yaml`. This is the curated staging file for
human review. Format:

```yaml
  <filename>:
    content_hash: <sha256>
    subdirectory: <subdir>
    title: "<title>"
    authors: "<authors>"
    published_date: "<YYYY-MM-DD>"
    comments: >-
      Why this paper is relevant to the project.
```

## Filename conventions

- Lowercase, hyphenated slugs: `repoagent-repo-level-doc-generation.pdf`
- Descriptive but concise: capture the paper's key contribution
- Always `.pdf` extension

## Batch ingestion

For multiple papers at once, write a download script following the pattern in
`scripts/download-doc-research-corpus.py` (async httpx with semaphore), then
run `scripts/backfill_article_metadata.py` to register metadata.

## Cloudflare / bot-protected sources

Some PDF hosts (FINRA, SEC, institutional sites) use Cloudflare or similar bot
protection that blocks `curl`, `httpx`, `requests`, and even `cloudscraper`.
The block is on TLS fingerprint (JA3 hash), not User-Agent headers.

**Solution**: `curl_cffi` with Chrome TLS impersonation.

```python
from curl_cffi import requests as cffi_requests
import os

session = cffi_requests.Session(impersonate="chrome")
r = session.get(url, timeout=30)
if r.status_code == 200 and (b"%PDF-" in r.content[:10] or "pdf" in r.headers.get("content-type", "")):
    with open(dest, "wb") as f:
        f.write(r.content)
```

Install into the project venv: `~/.venvs/universal/bin/pip install curl_cffi`

**When to use**: any download that returns HTTP 403 with `text/html` content-type
when the URL should serve a PDF. Try normal `httpx`/`requests` first; fall back
to `curl_cffi` on 403.

## What NOT to do

- Do not add download logic to any service (services have no outbound access)
- Do not manually insert into `rag_metadata.db` when the API is available
- Do not skip the content_hash — it's the join key for query-time enrichment
- Do not write a hand-curated markdown summary file in place of the PDF and
  call it "ingestion" — that's transcription. Save the PDF bytes; put any
  agent-authored notes in a sibling `*-notes.md` clearly labeled as such

## Content-Integrity Invariant (HARD — applies to all corpus types)

**Invariant**: ∀ ingested artifact: after download, verify the served bytes
contain the document the metadata claims. A successful HTTP 200 + valid PDF
magic bytes + non-empty text layer is NOT proof of identity — opinion-ID
collisions, mis-mapped Harvard PDF stores, and stub/preview substitutions
happen and pass every format check.

**Motivating failure** (thread 947, 2026-05-12): CourtListener
`harvard_pdf/5452512` was ingested as `mcdonald-v-antelope-valley-2008.pdf`
(*McDonald v. Antelope Valley CCD* (2008) 45 Cal.4th 88). The downloaded PDF
parsed cleanly to 24,246 chars of text — but the text was *Matter of Tinker*,
a 1926 New York Appellate Division opinion. Format checks (200 OK, valid
PDF, text layer present, `md_list` returns sections) all passed; the wrong
opinion sat in the corpus undetected until a downstream pinpoint query
returned nonsense.

**Required post-download check** — run before declaring ingestion successful:

1. Extract the first ~2 KB of text from the downloaded artifact
   (`fs(op="read", ...)` for small files; `fs(op="md_read", section="<first heading>", ...)`
   for large PDFs).
2. Verify **at least two** of the following identity tokens are present in
   that text:
   - **Case name** — at least the distinctive party (e.g. "Antelope Valley",
     "Larson", "Mansell"). Plaintiff-v-defendant order is often inverted in
     opinion captions; match on the rarer surname/entity.
   - **Citation** — volume + reporter + page (e.g. "45 Cal.4th 88",
     "213 Cal.App.3d 324"). The reporter abbreviation is the strongest signal.
   - **Date / year** — the published year from the metadata (e.g. "2008",
     "1989"). A year off by decades is the canary that fired on McDonald.
   - **Court** — issuing court name (e.g. "Supreme Court of California",
     "Court of Appeal, … District").
3. If <2 tokens match → STOP. Do not register the artifact. Surface the
   mismatch with: source URL, expected metadata, and the first ~500 chars of
   the extracted text. Investigate the source ID (CourtListener opinion-ID
   collisions are common; search by case name to find the correct ID).
4. If the artifact is a multi-document PDF or contains only a stub, treat
   the same as a content mismatch.

**Sequencing**: this check runs after step 3 (Download and register) and
before step 4 (Verify). For batch ingestion, the check MUST run per-file —
a single bad ID in a list of nine destroys downstream verification trust
until detected.

**Why two tokens**: a single token (e.g. just the year, or just a common
surname) is insufficient — *Matter of Tinker* itself contains numerous
4-digit years and common legal surnames. Two independent identity dimensions
(name + citation, or citation + court) is the minimum to rule out
coincidental matches.

## Anti-Transcription Invariant (HARD — applies to all corpus types)

**Invariant**: ∀ corpus entry: the text in the committed artifact MUST be
derived from a downloaded source file on disk. ¬ transcribe verbatim text
from agent context-window web fetches (WebFetch tool responses, cached tool
files in `/home/io/.cursor/projects/.../agent-tools/`). These are read
artefacts, not provenance artefacts.

**The correct sequence is always**:
1. Download source bytes to disk (`scripts/ingest-article`, `curl`, or browser save)
2. Read the downloaded file from disk using the `fs` MCP tool (see below)
3. Build the corpus entry from that disk read

**Why**: Content fetched by the WebFetch tool may be cached, truncated, or
differ from the source served bytes. Provenance requires a physical file at a
known path, not a reconstruction from an agent tool response.

## Reading Downloaded Sources (fs MCP — canonical path)

Use `fs(sandbox="workspaces", ...)` to read downloaded source files.
All paths must be prefixed with the repo name:
`universal-llm-gateway/docs/research/<subdir>/<filename>`.

### PDFs (preferred: structured section access)

`fs` auto-converts PDFs via `pymupdf4llm.to_markdown()` at read time.
Use `md_list` + `md_read` for section-level access to large opinions:

```python
# Get the section/TOC structure
fs(sandbox="workspaces", op="md_list",
   path="universal-llm-gateway/docs/research/<subdir>/<slug>.pdf")

# Read a specific section by heading
fs(sandbox="workspaces", op="md_read",
   path="universal-llm-gateway/docs/research/<subdir>/<slug>.pdf",
   section="<Section Heading>")

# Read the full converted text (small PDFs)
fs(sandbox="workspaces", op="read",
   path="universal-llm-gateway/docs/research/<subdir>/<slug>.pdf")
```

For tabular/columnar PDFs (invoices, statements), prefer `finance_extract_pdf(path=...)` via MCP which uses `pdfplumber` and preserves table structure.

### HTML (court opinions, web pages)

`fs(op="read")` on `.html` files returns raw HTML markup — it does NOT
auto-strip tags. `md_list` will find structural sections only if the HTML
uses real `<h1>`–`<h6>` headings (most court sites use `<strong>` or
`<p>` instead, yielding only a single `[Preamble]` section).

**Correct approach for HTML court opinions**:

```python
# Full raw HTML (then grep/search for opinion text between known tags)
fs(sandbox="workspaces", op="read",
   path="universal-llm-gateway/docs/research/<subdir>/<slug>.html")

# Or: targeted grep on the downloaded file
Grep(pattern="<target text>", path="docs/research/<subdir>/<slug>.html")
```

The opinion body in SCOCal HTML is always inside `<div id="opinion">…</div>`.
Pin-cite page markers appear as `<strong>[N Cal.Xd NNN]</strong>` inline.
Extract the relevant paragraphs from that div for Key Quotations with pin cites.

## Case Law Workflow — Source Format Priority

**Invariant**: ∀ case law download: exhaust non-HTML formats before accepting HTML.
PDF is strongly preferred because `fs md_list`/`fs md_read` work on PDFs via
`pymupdf4llm` — structured section access, no tag-stripping required. HTML requires
grep-based extraction and is a fallback only when no PDF exists.

### Format preference order (try in sequence, stop at first success)

1. **CourtListener Harvard PDF** — `https://storage.courtlistener.com/harvard_pdf/<opinion_id>.pdf`
   - Find `opinion_id` via `https://www.courtlistener.com/?q="<case name>"&type=o`
   - Harvard corpus covers most published state appellate + Supreme Court opinions
   - No auth required; direct download works
   ```bash
   scripts/ingest-article --url https://storage.courtlistener.com/harvard_pdf/<id>.pdf \
       --subdir <subdir> --filename <case-slug>.pdf \
       --title "<Case Name (year) citation>" --date <YYYY-MM-DD>
   ```

2. **CourtListener other PDF** — check the opinion page for a PDF download link
   (e.g. `storage.courtlistener.com/pdf/...`); same download pattern as above

3. **Official court PDF** — California Courts, federal PACER, or publisher PDF
   if directly linkable without auth

4. **SCOCal HTML** (California Supreme Court only) — Stanford archive, static HTML,
   no JavaScript required; reliable for pre-2000 CA Supreme Court opinions
   ```bash
   scripts/ingest-article --url https://scocal.stanford.edu/opinion/<slug> \
       --subdir <subdir> --filename <case-slug>.html \
       --title "<Case Name (year) citation>" --date <YYYY-MM-DD>
   ```

5. **CourtListener HTML** — requires JavaScript; use cursor-ide-browser MCP
   (`browser_navigate` → `browser_evaluate` to extract `document.body.innerText`)
   → write to `.html` file

6. **User-prompt fallback** — if all automated paths fail (CAPTCHA, auth wall,
   JS block on all paths): stop and prompt the user per the User-Prompt Fallback
   Protocol below

**Never** substitute Justia or vLex HTML for the primary source. They are not
authoritative; CourtListener and SCOCal are. If the authoritative source is
inaccessible, surface the blockage to the user.

## User-Prompt Fallback Protocol

When the agent cannot download a source automatically (JS wall, CAPTCHA, auth,
403 on all paths), prompt the user with:

1. The exact source URL to download from
2. The exact destination path to place the file
3. What to do after: re-run the ingestion step or notify the agent to continue

Example prompt:
> I cannot download `<URL>` automatically (requires browser authentication).
> Please download this file and save it to:
> `docs/research/<subdir>/<filename>`
> Once placed, I will read it and complete the corpus entry.

---

## Related cortex skills

- `cortex:agent-skills/legal-opinion-corpus-ingestion.md` — sibling discipline for case-law / statute primary sources; the Anti-Transcription and Content-Integrity invariants above apply identically there.
- `cortex:agent-skills/thirdparty-api-mirror.md` — adjacent primary-source pattern for vendor API docs (`docs/thirdparty/{provider}/upstream/` + summaries).
- `cortex:agent-skills/corpus-cross-reference-discipline.md` — upstream discipline on intake-side retrieval and writing-side identifier surfacing; covers what happens with the artifact AFTER ingestion when it gets cited.
