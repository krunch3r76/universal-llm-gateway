---
name: produce-uml
description: "Produce architecture UML as HTML+SVG or activity/sequence/component diagrams."
trigger_match_terms: ["produce-uml", "produce_uml", "produce", "architecture", "uml", "html+svg", "activity", "sequence", "component", "diagrams."]
---

**Default deliverable:** an **HTML viewer + SVG diagrams + markdown source** under `tmp/<slug>/`.
Markdown-only SVG embeds are secondary (docs commits, GitHub). HTML is the operator-facing artifact.

## When to load

- User asks to render/diagram/UML a plan, pipeline, architecture, or agent-bus design
- User says `@produce-uml`, "render to html or uml", or references PlantUML
- User wants a **new-developer guide** with problem→solution narrative and operator/agent relevance (use § Narrative guide below)
- Also read `/mnt/torus/projects/.cursor/rules/uml.mdc` for syntax pitfalls (glob-matched on `docs/`)

## Output layout (mandatory for `tmp/` renders)

Create **one new directory per request** at workspace `tmp/<slug>/`:

```
tmp/<slug>/
  <slug>.html                 # PRIMARY — open in browser
  <slug>-activity.md          # source of truth: prose + PlantUML in <details>
  images/<slug>-activity/
    <diagram-name>.svg        # rendered vectors (verify each)
```

Markdown-only (no HTML) is allowed when the operator asks — same directory rule applies:

```
tmp/<slug>/
  <slug>.md                   # or <slug>-activity.md — stem must match images/ subdir
  images/<slug>/              # plantuml_helper uses {markdown_stem} as subdir name
    <slug>_diagram_01.{svg,png}
```

**Exemplar:** `tmp/email-capture-postprocess/` (HTML + zoom toolbar + activity + state SVGs).
**Markdown-only exemplar:** `tmp/agent-substrate-session-before-after/` (relative `images/<stem>/` links).

## Narrative guide (plans & cross-agent architecture)

Use this section when the source is an **implementation plan**, audit sweep, or agent-bus–converged design — not a single pipeline or ingest flow.

### Audiences (required prose in HTML + markdown)

| Block | Purpose |
|---|---|
| **Audience routing table** | Links: operator (human principal), agents, new devs, implementers |
| **For the operator (operator)** | Why it matters to him; what warnings/noise mean; **checkpoint table** (Phase 0/1/2 — what approval is / is not); paired decisions kept separate; provenance tie-in (governing skill, assertion, thread ID if known) |
| **For agents** | Table by seat (`web-anthropic`, `cursor`, batch seats): **Do / Do not**; governing `agent_skill:*`; agent-bus thread; push reminder if posting to web |
| **Primer** | 60s domain vocab for newcomers (only if needed) |
| **“Backing” / audit terms** | Map shorthand to concrete fields + `assertions` row shape (what Checks 4/5 SQL actually test); note what is *not* backing (`source_uri` alone, etc.) |
| **Problem → bad fixes → solution** | Lead with **cause chain** when applicable (e.g. birth default → incomplete confirmed row → audit fires → warning scale); then amplifiers (detector scope); reject anti-patterns explicitly |
| **Worked example** | One concrete entity/case showing non-obvious route (e.g. Route C) |
| **Diagrams** | Order: problem → solution steps → proof → **reference last** (component map collapsed in HTML) |

### Exemplar (plan + stakeholder narrative)

`tmp/skill-entity-auditor-validatability-sweep/` — `skill-entity-auditor-validatability-sweep.html` + `-activity.md`.

Pipeline-only exemplars (`tmp/email-capture-postprocess/`) omit operator/agent sections unless the user asks for them.

## Workflow

1. **Read sources** — specs, code, agent-bus threads, cortex entities (for plan/todo graphs).
2. **Write** `tmp/<slug>/<slug>-activity.md`:
   - If plan/cross-agent: full **Narrative guide** sections above
   - Else: prose summary + architecture tables
   - `![title](images/.../*.svg)` links above the fold
   - PlantUML source in `<details>` blocks (one ` ```plantuml ` block per diagram)
   - **Settled decisions only** — do not leave ambiguous forks in diagrams (no "open question" paths drawn as if decided)
3. **Render SVG:**
   ```bash
   $HOME/.local/bin/plantuml -tsvg -o tmp/<slug>/images/<slug>-activity/ ...
   # or: /mnt/torus/my-tooling/bin/plantuml_helper.py render tmp/<slug>/<slug>-activity.md
   ```
4. **Verify:**
   ```bash
   /mnt/torus/my-tooling/bin/verify-plantuml-svg.sh tmp/<slug>/images/<slug>-activity/*.svg
   ```
5. **Write HTML viewer** `tmp/<slug>/<slug>.html`:
   - Link to markdown source
   - For plans: **For the operator** and **For agents** sections before primer/diagrams (anchor ids `operator`, `agents`)
   - Scope callout (what is / is not in the plan)
   - Summary tables
   - Diagrams embedded via **`<object type="image/svg+xml">`** inside a zoom/pan wrapper — **not** `<img>` with `max-width: 100%`
   - Copy zoom toolbar + JS from exemplar: `tmp/email-capture-postprocess/email-capture-postprocess.html` (layout); plan narrative layout from `tmp/skill-entity-auditor-validatability-sweep/skill-entity-auditor-validatability-sweep.html`
6. **Tell the operator** the `file://` path to the **HTML** file (default), or the on-disk markdown path when markdown-only.

Markdown-only closeout line (on-disk path — not portal):

`file:///mnt/torus/projects/universal-llm-gateway/tmp/<slug>/<slug>.md`

## HTML vs markdown

| Surface | Use | Why |
|---|---|---|
| **HTML + SVG** | Default for `tmp/` plan/architecture renders | Zoom/pan, callouts, tables, scope clarity; best review UX; portal-safe (single file or co-located assets via `<object>`) |
| **Markdown + images** | `tmp/` session notes, operator md viewer | `plantuml_helper.py` embed workflow; **file-relative** `images/<stem>/…` only |
| **Markdown + SVG in `docs/`** | Committed artifacts, GitHub | Same relative layout under doc parent |

## Markdown image paths (CRITICAL — tmp / external viewers)

Browser and desktop markdown viewers resolve `![](…)` **relative to the markdown file's directory** on disk. Format (SVG vs PNG) is usually fine; **path shape** is what breaks.

| ❌ Avoid | ✅ Use |
|---|---|
| `![](file:///mnt/…/image.png)` | `![](images/<stem>/diagram_01.png)` |
| `![](/tmp/…/image.png)` workspace-absolute | File-relative `images/<stem>/…` next to the `.md` |
| Markdown at `tmp/foo.md` with images at `tmp/images/foo/` | Co-locate: `tmp/foo/foo.md` + `tmp/foo/images/foo/` |
| Opening only the `.md` via XDG portal (`file:///run/user/1000/doc/…`) | Open on-disk path: `file:///mnt/torus/projects/…/tmp/<slug>/<slug>.md`, **or** use the HTML viewer |

**Portal rule:** File-manager / sandbox open copies **only** the markdown file into `/run/user/1000/doc/<uuid>/`. Sibling `images/` is not copied — relative links break. Fix: open the real checkout path, or use `<slug>.html` (default).

**`plantuml_helper.py`:** renders to `{md_parent}/images/{md_stem}/` and writes matching relative links. After render:

```bash
/mnt/torus/my-tooling/bin/plantuml_helper.py render tmp/<slug>/<slug>.md
/mnt/torus/my-tooling/bin/verify-plantuml-svg.sh tmp/<slug>/images/<slug>/*.svg
```

For PNG output (optional): extract `.puml` from `<details>` and `plantuml -tpng -o images/<stem>/ …`.

## SVG embedding in HTML (critical)

SVG is vector — zoom works when embedded correctly.

| ❌ Avoid | ✅ Use |
|---|---|
| `<img src="…svg" style="max-width:100%">` | `<object type="image/svg+xml" data="…svg">` in `.diagram-stage` |
| Browser zoom only | Toolbar: +/−/Reset/Open SVG + Ctrl+wheel scale + drag pan |
| Ambiguous auto-queue vs manual paths | Label triggers explicitly (Manual / Automatic / Deferred) |

Open raw SVG in a new tab (`Open SVG` button) for unlimited native browser zoom.

## Diagram types

| Type | Use |
|---|---|
| Activity | Ingest flows, pipelines, request lifecycles |
| Sequence | API/MCP call chains |
| Component | Architecture overview |
| State | Entity lifecycle, workflow_state queues |

For **plan/todo arcs**, add a cortex entity graph: query `entity_get` on plan + todos; draw `child_of` (solid), `depends_on` (dashed), `related_to` (dotted).

## PlantUML syntax pitfalls

| ❌ Avoid | ✅ Use |
|---|---|
| `list[str]`, `dict[str, Any]` in labels | `List`, `dict` |
| Mix object/rectangle styles | `component` or `[artifact]` consistently |
| Chained arrows `A --> B --> C` | Separate arrows |
| Markdown tables inside PlantUML | Rectangles/notes |
| Em-dashes in labels | Hyphens or `\n` |
| `allowmixing` without need | Add only when mixing stereotypes |

## Render tooling

| Tool | Path | Role |
|---|---|---|
| `plantuml` CLI | `~/.local/bin/plantuml` | `-tsvg -o <dir> file.puml` |
| `plantuml_helper.py` | `/mnt/torus/my-tooling/bin/plantuml_helper.py` | Markdown embed/render/restore cycle |
| `verify-plantuml-svg.sh` | `/mnt/torus/my-tooling/bin/verify-plantuml-svg.sh` | Syntax gate after render |

## Operator prompts

`@produce-uml sources=<paths> slug=<name>` — HTML + SVG under `tmp/<name>/`. For long
prompts, specify diagram types and any **settled** scope constraints (e.g. "manual-first, no auto-queue").

## Diagram scope (CRITICAL)

When architecture has **multiple lifetimes** (ingest vs pipeline vs reviewer session), use **separate diagrams** with explicit scope labels — never one activity chart chaining them on one timeline.

| Diagram | Shows | Must NOT include |
|---|---|---|
| Ingest | Bridge capture → entity → stop | GET /entities, pipeline, reviewer |
| Pipeline | DAG when invoked | Ingest, queue pickup GET |
| Reviewer session | Queue pickup → actions | Ingest steps, pipeline resolve internals |

Label two different `GET /entities` calls explicitly:
- **Queue pickup:** `?type=correspondence&workflow_state=pending_postprocess`
- **Pipeline resolve:** `?limit=500` (known_entities for prompt)

Exemplar: `tmp/email-capture-postprocess/` (four scoped diagrams).

## Related paths

| Path | Role |
|---|---|
| `/mnt/torus/projects/.cursor/rules/uml.mdc` | Syntax/types stub for `docs/` globs |
| `tmp/email-capture-postprocess/` | Scoped HTML+SVG exemplar (ingest / pipeline / reviewer / lifecycle) |
| `tmp/skill-entity-auditor-validatability-sweep/` | Plan UML + newcomer narrative + operator/agent sections |
| `tmp/agent-substrate-session-before-after/` | Markdown-only + co-located `images/` + relative links |

Supersedes scratch notes `tmp/keep/agent-guides-scratch/produce_uml.md` and `uml.md`.
