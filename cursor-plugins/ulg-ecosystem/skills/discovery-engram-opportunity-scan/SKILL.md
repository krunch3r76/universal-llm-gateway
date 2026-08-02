---
name: discovery-engram-opportunity-scan
description: "After substantive ULG coding arcs — scan capture gaps before close; hub = canonical discoveries/engrams; satellite = local discoveries; platform → hub."
trigger_match_terms: ["discovery", "engram", "insight", "discoveries", "tasks/journal", "knowledge capture", "revisit", "expand", "substrate", "recurrence"]
related_skills: ["completion-provenance-discipline", "friction-review", "path-sim", "orchestrator-workflow"]
---

# Discovery + engram opportunity scan

**Scope:** `ulG_ecosystem ∧ coding_arc` — hub + satellites on shared plugin. `¬(life_matter ∨ legal_case ∨ personal_diary)` → life/matter playbooks.

**Invariant:** `substantive_arc ∧ durable_insight ⇒ scan_before_close`. `CHECKPOINT ∨ bus_turn ∨ todo` alone `⇏` workshop capture.

## Corpus partition — bind first

`wrong_path ⇒ silent_non_capture`. Hub repo = `universal-llm-gateway`.

| Surface | Hub | Satellite |
|---|---|---|
| Discoveries | **Canonical** `…/tasks/discoveries/` | Service-local `…/{repo}/tasks/discoveries/` |
| Engrams | **Only** `…/docs/engram/` | `¬∃ docs/engram/` — platform engrams → hub |
| Journal | Active checkout `tasks/journal/` | Same |
| ULG dev story (future) | Aggregates hub corpus | Local discoveries only |

```
platform_insight ∨ cross_cutting_ULG ⇒ hub_path(discovery | engram)
service_local ⇒ satellite_path(discovery)
ambiguous ⇒ hub_default
satellite_arc ∧ platform_lesson ⇒ hub_capture ∨ hub_todo (¬ orphan satellite slug)
hub_checkout ⇒ full_scan(discovery_dedup ∧ engram_dedup ∧ journal)
```

Checkout detect: workspace root / `AGENTS.md` / `fs list {repo}`. Use `satellite-workspace` for plugin mechanics.

## Trigger / skip

**Load** before session close, handoff, or orchestrator done-claim when **any**:

`expand ∨ detent≥wide ∨ recurrence(≥2) ∨ patch_post_incident ∨ arch_insight ∨ design≠runtime_gap ∨ operator_asks_capture`

**Skip:** `trivial ∨ typo_only ∨ no_generalizable_lesson ∨ already_filed_this_session`

## Channel ladder — one primary per insight

| Predicate | Channel | Where |
|---|---|---|
| reusable arch insight | discovery | `tasks/discoveries/{slug}.md` + `index.yaml` |
| general design/research pattern | engram | hub `docs/engram/{slug}.md` only |
| arc narrative | journal | `write_journal_entry` / `session_close` |
| operator correction | lesson | `tasks/lessons/` (`lessons.mdc`) |
| codified defect | friction + todo | `cortex(friction)` → investigate→execute |
| ratified contract | decision + spec | `decision:` + `cortex://notes/system/specs/` |
| transient / smoke pass | nothing | commit body |

`stranger_30d_benefit(assert) ⇏ true` → discovery/journal, not assert (`cortex-essentials`). `¬duplicate_channels` — cross-link in `Related:`.

## Scan (binding order)

1. **Inventory** — ≤5 candidates from CHECKPOINT/scoreboard/frictions/chat; tag `discovery|engram|journal|filed|skip`.
2. **Dedup discoveries** — hub: read `universal-llm-gateway/tasks/discoveries/index.yaml`. Satellite: read `{repo}/tasks/discoveries/index.yaml`; `platform_insight ⇒` also read hub index. `∃ related_slug ⇒ extend ∨ link` — ¬ mint duplicate.
3. **Dedup engrams** — hub only (or platform filing from satellite): `fs list universal-llm-gateway/docs/engram` + `rag(scope=project, …)`. `todo:cortex-engram-writing-workflow` = formal authoring still open.
4. **Heuristics → capture**

| Signal | Capture |
|---|---|
| patch outside ratified contract/tests | discovery + friction |
| path-sim design-time OK, runtime thrash | discovery |
| model limit + control run | discovery (+ model/runtime) |
| consult emergent role split | engram ∨ discovery |
| gate `if` without vocabulary | discovery + expand todo |
| operator expand bind | journal + todo/decision |

5. **File** — correct partition path; discovery body: Observation · Evidence · Implication · Related (+ frontmatter rows in index). Engram: hub only, ¬ commit.
6. **Closeout** — one line: `hub|satellite` + channel + slug/path, or `none — on graph`.

## Composes

`awareness_ulg` · `expand-growth-loop` (distill→skill/rule = outer loop) · `orchestrator-workflow` (CHECKPOINT ≠ capture) · `completion-provenance-discipline` (filed ⇒ path evidence).

## Anti-patterns

| ✗ | ✓ |
|---|---|
| CHECKPOINT only for reusable insight | discovery + index row |
| assert for session narrative | journal |
| mint slug without index read | dedup first |
| engram for one-line bug | discovery ∨ friction |
| platform insight on satellite only | hub discovery |
| engram under satellite tree | hub `docs/engram/` |
| duplicate discovery + engram prose | one channel + cross-link |
