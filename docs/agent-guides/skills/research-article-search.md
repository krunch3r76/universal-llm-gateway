# Research Article Search — Dispatch Workflow

**Skill**: `research-article-search`  
**Complements**: `research-article-ingest` (ingest mechanics), `dispatch-workflow` (team_dispatch hygiene)

---

## When to use this skill

- Building or extending the RAG research corpus with new literature
- Running a literature survey on a topic for a task's recon arc
- Dispatching a multi-model search fan-out (gatherer → scope pass → ingest)

---

## The two-pass pattern

Discovery and scoping are distinct concerns executed by different models.

| Pass | Role | Model | MCP | Purpose |
|---|---|---|---|---|
| Discovery | `gatherer` | `openai/gpt-5.5` | `true` | Live web search via `web_search` + `browse` tools |
| Scoping | `reviewer` | Opus or GPT-5.5 | `false` | Apply operator bias, dedup, assign scopes |

¬ collapse them into one dispatch — the scoping model needs the discovery results as input.

---

## Step 1 — Author the search brief

Write a temporary brief at `/tmp/agent-bus-<slug>-search-brief.md`. Required sections:

1. **Your task** — one sentence stating the search goal
2. **Topic framing** — scope boundaries, what counts as in/out, critical angles to search separately
3. **Operator bias** — explicit criteria for what qualifies (effect size, named-model requirement, counter-evidence)
4. **Already seeded — DO NOT re-find** — list every arXiv ID and URL already in corpus
5. **Output format** — per-paper: title, authors+year, arXiv ID or URL, source type (peer-reviewed / arXiv / self-reported), named model(s) tested, research angle, key finding in one sentence

Operator bias MUST include:
- A bar for inclusion (e.g. "XML must prove STRONG named-model effect-sized comprehension advantage — not marginal")
- Named-model requirement (flag unnamed/self-reported clearly)
- Counter-evidence obligation (do not suppress findings that contradict the hypothesis)
- Contested numbers that need corroboration vs refutation

Load the existing seed from the sidecar on the orchestration thread:
```
fs(sandbox="cortex", op="md_read", path="notes/system/threads/<thread-sidecar>.md", section="Step 1 SEED")
```

---

## Step 2 — Discovery dispatch (live search)

**Only confirmed live-search path: `openai/gpt-5.5` with `mcp=True`.**

```python
team_dispatch(
    op="generate",
    role="gatherer",
    model="openai/gpt-5.5",
    contract="light-bounded",
    mcp=True,
    max_tool_turns=15,
    dispatch_thread_id="<orchestration-thread-id>",
    caller_agent="cursor",
    tags=["project:<slug>", "type:research-search", "run:mcp-enabled"],
)
```

GPT-5.5 receives `web_search` and `browse` tools via Vortex client-push injection. Confirm
`inline_only=false` and `tool_surface="mcp"` in the dispatch response before waiting.

**Models that do NOT work for live search:**

| Model | Issue | Confirmed |
|---|---|---|
| `anthropic/*` API seats with `mcp=True` | `tool_search` times out at 300s; no live web path | 2026-06-29, friction 21132 |
| Any model with `mcp=False` | Parametric recall only | All sessions |
| `grok-web` | Deprecated — SuperGrok Heavy subscription expired | 2026-06 |
| `xai/grok-4.3` API with `mcp=True` | Untested for live search; returned parametric in prior run | 2026-06-29 |

If the task requires multiple search perspectives, fan out a second gatherer with a distinct
search angle (e.g. "skill-salience angle" vs "format-comparison angle") as a separate dispatch.

---

## Step 3 — Wait for discovery results

```python
agent_bus(tool="wait", arguments={
    "thread": "<dispatch-thread-id>",
    "after_turn": 1,
    "wait_seconds": 120,
    "completion": "first_reply_from",
    "from_agent": "gatherer"
})
```

If the result shows only parametric recall (no `web_search` calls visible, all IDs are pre-2024
for a current-literature task), do NOT ingest — re-dispatch with `mcp=True` confirmed active.
Evidence of live search: arXiv IDs from the current year, novel framing not in training data,
explicit "I searched arXiv for…" language, or tool-use acknowledgment in the response.

---

## Step 4 — Scoping pass (reasoning model)

After raw candidates arrive, dispatch a reasoning-tier review. This pass:
- Applies the operator bias from the brief
- Deduplicates against seeded articles
- Assigns each candidate a subdir + RAG scope
- Flags unnamed-model or self-reported sources explicitly
- Rates ingest priority (HIGH / MEDIUM / LOW) with justification
- Surfaces counter-evidence finds prominently (¬ bury them)

The scoping model operates on already-returned text — `mcp=False` is acceptable here.

```python
team_dispatch(
    op="generate",
    role="reviewer",
    model="anthropic/claude-opus-4-8",   # or openai/gpt-5.5
    reasoning_effort="low",
    contract="light-bounded",
    mcp=False,
    dispatch_thread_id="<orchestration-thread-id>",
    caller_agent="cursor",
    tags=["project:<slug>", "type:scope-pass"],
)
```

Include the full discovery output + operator bias in the prompt. Ask the scoping model to
produce a structured table: `arXiv ID | filename slug | subdir | scope | priority | reason | flag`.

---

## Step 5 — Scope org review (optional, web-claude)

For material corpus extensions (>5 papers, new scope candidates), post the scoping-pass output
to the orchestration thread and dispatch a web-claude `op=handoff` to:
- Confirm or revise scope assignments
- Rule on whether new scope warrants creation vs folding into existing
- Flag papers to drop as peripheral

New scope creation requires edits to three places:
1. `scripts/backfill_article_metadata.py` — add `SUBDIRECTORY_TO_SCOPE` entry
2. `~/.gateway/rag.yaml` — add scope block under `scopes:`
3. Composite scopes (`all_corpus`, `research`) — add new scope prefix

---

## Step 6 — Ingest

Per confirmed and scoped candidates, execute `scripts/ingest-article` per `research-article-ingest` skill:

```bash
scripts/ingest-article --arxiv <ID> --subdir <subdir> --filename <slug>.pdf \
    --title "<title>" --authors "<authors>" --date <YYYY-MM-DD> --scope <scope>
```

Verify each paper passes content-integrity before indexing (≥2 identity tokens from disk read).

For batch ingestion, write an async download script following `scripts/download-doc-research-corpus.py`.

---

## Step 7 — Verify corpus coverage

```python
rag(op="coverage")
```

∀ paper: confirm scope's `indexed_files` count increased and `last_indexed` timestamp is fresh.

**¬ use semantic search for verification.** `qwen3-embedding-8b` may be cold/evicted from VRAM —
`chunks_found=0` with 60s duration is indistinguishable from "not indexed." Use `/coverage`
(sqlite-backed, immediately consistent) + "Index complete" log chunk counts.

---

## Scope directory table

| Subdir | Scope | Content |
|---|---|---|
| `llm/prompting` | `llm_prompting` | Prompt engineering, format effects, instruction following for large models |
| `prompting` | `small_llm_prompting` | Prompt engineering for small/local models |
| `workflows` | `workflows` | Pipeline architecture, agent orchestration, long-context instruction following |
| `rag-systems` | `rag_systems` | RAG architecture, evaluation, benchmarks |
| `software-agents` | `software_agents` | Agent workflows, multi-agent SE |
| `knowledge-management` | `knowledge_systems` | PKM, agent memory |
| `graph-modeling` | `graph_modeling` | Property graphs, KG construction |
| `belief-consistency` | `belief_consistency` | Belief revision, contradiction handling |
| `information-extraction` | `information_extraction` | NER, relation extraction, structured output |

For input-format-comprehension papers (XML vs JSON vs Markdown as model input, context salience,
token efficiency): use `llm/prompting` → `llm_prompting` unless volume warrants a dedicated scope.

---

## Worked example — P1 Track 3 (LLM input-format comprehension)

**Discovery**: GPT-5.5 with `mcp=True` on thread 3557 (2026-06-29). Returned 17 candidates
including 8 new arXiv IDs. Live search confirmed: 2025/2026 papers, novel PRISMA study,
Format Tax framing not in training data at time of dispatch.

**Key finding**: The 2025 PRISMA study (arXiv:2511.16707) ran Markdown, JSON, XML, and plain
text side-by-side in a systematic review task — **no significant difference among formats
(p > 0.9)**. This is the strongest counter-evidence to the XML superiority claim found in the
literature.

**What didn't work**:
- `anthropic/claude-opus-4-8` with `mcp=True` → `tool_search` 300s timeout; parametric recall
- `xai/grok-4.3` with `mcp=True` → parametric recall (tool surface inactive)
- Any model with `mcp=False` → parametric recall throughout

**Scoping pass**: Opus (`reasoning_effort=low`, `mcp=False`) on thread 3558 confirmed corpus
gap: `research` scope contained only structured-output papers, zero input-comprehension.
Endorsed ingest priority ordering. Also caught that no live tool access on this seat.

**Ingest queue**: 8 papers across `llm/prompting` (HIGH: 2411.10541, 2511.16707, 2604.03616)
and `workflows` (MEDIUM: 2411.07037, 2406.15981, 2401.18058).

---

## Related skills

- `research-article-ingest` — download + content-integrity + RAG indexing mechanics
- `dispatch-workflow` — team_dispatch hygiene, model strings, task shapes
- `cheap-recon-before-escalation` — when to use cheap search before escalating to Opus
