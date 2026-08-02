---
name: skill-document-writing
description: "When authoring, revising, retiring, or auditing a SKILL.md or agent_skill entity — SkillReducer workflow, compression, and format discipline."
trigger_match_terms: ["skill-document-writing", "skill_document_writing", "author", "revise", "retire", "compress", "compression pass", "skill reducer", "skill.md", "skill-authoring", "critique", "skill", "FOL pass", "skill_binding"]
related_skills: ["skill-authoring", "frontier-model-instructions", "agent-guidance-writing"]
---

# Skill Document Writing

## SOT authority chain (edit here first)

| Layer | Path / surface | Role |
|---|---|---|
| **Catalog** | `config/skills.yaml` | Sole placement authority — `surface_class` + `mcp_surface_required` (+ optional aliases) |
| **SOT (edit)** | `.cursor/skills/{slug}/SKILL.md` (shared_sync / cursor_only) · `.claude/skills/{slug}/SKILL.md` (life_local) | Authoritative body + L1 frontmatter — **only** hand-edit target |
| **Entity** | `agent_skill:{slug}` | Lookup / graph / frictions; `source_uri` + `surface_class` **project** the catalog (¬ parallel placement authority) |
| **Generated** | `.claude/skills/{slug}/SKILL.md` (shared_sync only) | Rendered by `gen_claude_bundles.py` — **NEVER** hand-edit when `surface_class=shared_sync` |
| **Propagate** | regen + `/claude-ai-sync` | Push catalog Claude.ai targets to Customize → Skills |

`edit ⇒ SOT file per catalog surface_class` → `ingest_skills.py` (entity sync) → `[surface_class=shared_sync] gen_claude_bundles.py` → `/claude-ai-sync`. Resolve write target via `entity_get` → `source_uri`; ¬ patch a managed shared-sync `.claude/skills` copy. Runtime discovery filters on `capabilities_required` (derived from `mcp_surface_required`), not seat-name metadata.

`gen_skill_stubs.py` projects/verifies **frontmatter + thin stubs** from entity metadata — ¬ body authorship. Full bodies live under `.cursor/skills/` (or life-local `.claude/skills/`); do not read its "cortex SOT" wording as license to author bodies in cortex or generated `.claude/skills` copies.

## Load order

Fixed-overhead URIs — load before any substantive pass:

- Research digest — `cortex://notes/system/references/skillreducer-research-excerpts.md`
- Workflow map — `cortex://notes/system/references/skill-compression-workflow-map.md`

| Task | Read, in order |
|---|---|
| Trivial edit (typo, single rule, metadata) | this skill |
| Substantive authoring / revision | `frontier-model-instructions` → this skill → research digest → workflow map |
| Compression pass | substantive-authoring order + target SOT + backup |
| Cursor `.mdc` / line budget | + `agent-guidance-writing` |

`substantive ⇔ new_section ∨ taxonomy_reclassify ∨ trigger_rewrite ∨ ≥1_rule_changed`. The research digest + map are not compression-only; they carry the taxonomy and gates that govern authoring too.

## Fixed overhead admission gate

`substantive_pass ∨ compression_pass ⇒ fixed_overhead_read_live` **before** classify or edit. `packet_corpus_present ⇏ fixed_overhead_satisfied` — a rich packet supplies domain corpus, not the SkillReducer method layer; the read is not discharged by "the packet has enough." Canonical failure: thread 4034 (`skill:todo-lifecycle` rewrite) authored from priors + packet corpus only, skipped the digest/map, and dropped taxonomy-governed detail (3 merged frictions + a wiring-gap ref) — caught only on operator review, forcing a redo.

**Fixed overhead (always `fs read`, never grep-only or skipped):**

| # | URI | Role |
|---|---|---|
| 1 | `cortex://notes/system/references/skillreducer-research-excerpts.md` | SkillReducer taxonomy + gates — **pre-distilled RAG surface** from scope `skill_compilation`; this file is how the RAG corpus is surfaced for authoring |
| 2 | `cortex://notes/system/references/skill-compression-workflow-map.md` | Per-slug steps, sidecar contract, Phase A–D post-edit pipeline |

**Admission (sidecar-enforced):**

1. **Read receipt** table (`path | sha256`) for **both** files — recorded **before** the Classification manifest. `sha256 = hash(bytes_read_this_pass)`, ¬ copied unverified from a prior sidecar; a hash that does not trace to a live read this pass is not a receipt.
2. Classification manifest cites ≥1 taxonomy class label from the digest (e.g. `core_rule`, `procedure_step`, `example` prior-override) — proves the digest was read, not path-guessed.
3. Gate-1 checklist completed against digest § Hard failure mode (rules embedded in examples → keep in L2).

**Dispatch packets:** any handoff whose `<task_guidance>` invokes skill-document-writing MUST list both fixed-overhead URIs in `<invariants>` — not buried only in this skill's load order. Packet author owns this (handoff-packet-authoring).

**Live RAG refresh (optional):** when the digest may be stale, `rag(op="search", scope="skill_compilation", …)` then diff against the digest before replacing it. Default path: `fs read` the digest — it is the operational surface, not a live RAG query per slug.

## Recursive closure

To rewrite ANY skill — including this one — run one loop: reference consult → classify body to taxonomy → rewrite SOT only → write result sidecar → post-edit by seat. `self_rewrite ⇒ identical_loop`. An Anthropic flat-upload seat runs the whole loop with `fs` + `cortex` + `agent_bus`; ¬ shell required. `seat ¬runs(entity_sync ∨ bundle_check) ⇒ hand off that step`, ¬ skip it.

## Skill lookup — file SOT for L1, entity for body + graph

**L1 `description:`** — frontmatter on the SOT file (`.cursor/skills/{slug}/SKILL.md`) is authoritative. `entity.description` mirrors it via `python scripts/cortex/ingest_skills.py`; `ingest_skills.py --check` fails on file⟷entity drift. One-time bootstrap for empty cortex frontmatter: `scripts/cortex/backfill_skill_frontmatter_descriptions.py`.

**Anthropic Customize / fleet description length:** Fleet policy caps YAML `description` at **200** characters on Cursor SOT — same ceiling for Anthropic Customize uploads and any future Anthropic Skills API inject. Anthropic API/spec allow 1024; we do not use that headroom (`MAX_SKILL_DESCRIPTION_LEN`). SOT: `decision:claude-ai-skill-description-limits-by-surface`. Also: no angle-bracket XML-like tags in `description` (Customize UI rejects).

**Body + graph** — `agent_skill:{slug}` entities remain the lookup surface for full body, search (`cortex(tool="entities")` matches `description` substring globally), edges, and frictions.

- **Read body:** `cortex(tool="entity_get", arguments='{"entity_id": "agent_skill:{slug}", "intent": "body"}')` returns body + `source_uri` — or `fs read` the `source_uri` directly.
- **Annotate in place:** assert frictions / observations / edges on `agent_skill:{slug}` itself (`cortex(tool="assert"|"friction"|"relationship_create", …)`); ¬ scatter them in prose or sidecars when the entity can hold them.
- **Edit bytes:** `entity_get`/`resolve` → `source_uri`, then `fs(op="write"|"replace", path=source_uri)`; re-run ingest to sync entity fields.
- **Fallback only:** `entity missing ∨ unregistered ⇒` resolver.py path order (workflow map Phase B). ¬ assume a legacy cortex mirror path.
- ¬ RAG a live skill SOT: skills are delivered server-side (cursor from indexed dirs, Anthropic Customize from the attached bundle); live-SOT RAG is stale and disabled. RAG is not the lookup path.

## Decision gate

- `author ⇔ recurring ∧ past_judgment_failed`; speculative skills die.
- Before draft: choose `skill_class` and scan trigger overlap.
- Versioning: major bump for `skill_class` or trigger rewrites; minor otherwise.
- Retire with deprecation header + `supersedes` relationship; ¬ delete.

## CANDIDATE SKILL REVISION (audit-visible)

Parked skill-revision proposals are **assertions** whose claim begins with the literal prefix `CANDIDATE SKILL REVISION` on `agent_skill:` / `rule:` / `skill:` entities. They are **audit-visible**: GRAPH_ONLY detector `agent_skill_revision_candidate_unadjudicated` warns while any such assertion is active (`superseded_by IS NULL`) and `review_status ∉ {rejected, committed}` (`libs/cortex_store/dispatch_ops/_detectors/skill_revision_candidate.py`). v1 matches claim-prefix only.

**∀ next skill touch** of that entity (author / revise / compress / retire / substantive review):

1. **Discover** — `cortex(tool="assertions", …)` on the entity or `cortex(tool="audit", …)` and scan for `agent_skill_revision_candidate_unadjudicated`.
2. **Adjudicate before closing the touch** — leave no open candidate behind:
   | Disposition | Action |
   |---|---|
   | Accept | Apply the revision to SOT → `ingest_skills.py` → set candidate `review_status=committed` **or** `supersede` with a non-candidate applied claim |
   | Reject | `assertion_update(review_status="rejected", review_notes=…)` (or supersede with rejection rationale) |
   | Still open | Supersede with a refined `CANDIDATE SKILL REVISION …` claim only when work remains; ¬ silent skip |
3. **Seed new candidates** with that exact claim prefix when parking a revision for later — so audit keeps them visible until adjudicated.

Anti-pattern: edit the skill body and ignore open `CANDIDATE SKILL REVISION` rows — audit will keep warning and the parked judgment is lost.

## L1 / L2 / L3 disclosure

| Layer | Location | Keep |
|---|---|---|
| L1 | frontmatter `description:`, `trigger_match_terms`, optional `trigger_short` | Precise imperative trigger. **Frontmatter `description:` is SOT**; entity mirrors via ingest. Discovery substrate = plain `trigger_match_terms` + `entities()` description substring; `trigger_short` is display/compression only, ¬ sole match surface. |
| L2 | SKILL body | Always-loaded procedure + operational rules. FOL by default. Multi-gate workflows lead with `## FOL pipeline`. |
| L3 | Sibling files, sidecars, commits | History, rationale, happy-path examples, edge-case catalogues; anything not changing behavior. |

`density ∝ 1/adherence`: bloated L2 degrades compliance; prefer lean L2 + on-demand L3. **Flat-upload caveat:** Anthropic Customize bundles inject L2 whole with no runtime `read_file` — L2 must be operationally self-sufficient; ¬ defer a load-bearing rule to an L3 file the seat cannot open mid-task.

## Body taxonomy

Classify every section before rewriting (SkillReducer Stage 2). Evidence + detail: `cortex://notes/system/references/skillreducer-research-excerpts.md`.

| Class | L2 action |
|---|---|
| `core_rule` | Keep; compress to FOL where natural |
| `procedure_step` | Keep; imperative steps + FOL gates |
| `example` | Keep iff prior-override / failure-targeting; else L3/delete |
| `background` | L3/delete |
| `template` | L3 unless required at execution time |
| `redundant` | Delete |

`mode=compress ⇒ preserve(normative_content) ∧ ¬add_content ∧ flag(deviations) ∧ relocate(unique_removed_content)`. Authoring mode may add load-bearing content; flag it as an intentional grow, ¬ silent bloat.

**Gate 1 (faithfulness):** every operational concept in the original appears in revised L2 ∪ relocated L3. Per-type rollback on miss. Necessary, not sufficient.

**Gate 2 (retention) — implicit-rule failure mode:** rules embedded in examples are the dominant regression; the classifier relocates them but the agent needs them in always-loaded L2. On any such item, promote back to L2 **in original form** (¬ recompress). Do not strip bad/good pairs from prior-override guardrails (provenance, git revert, done-claims). Reference catalogs (`cortex`, `fs`): FOL on behavioral gates only, ¬ op tables.

## Authoring & compression pass

Per slug, one at a time:

1. Load fixed overhead. Resolve the target skill by entity URI (§ Skill lookup) → `fs read` its `source_uri` (+ backup for diff); ¬ assume the SOT path.
2. Classify each section → taxonomy → L2 keep / L3 relocate / delete.
3. Rewrite SOT only via `fs(op="write"|"replace", …)`; never edit generated `.claude/skills/*/SKILL.md`.
4. Write result sidecar: `cortex://notes/system/threads/compression-results/{slug}.md` (skeleton below).
5. Batch/arc: reply `DONE {slug}` on the coordination thread; ratify with a wave sidecar.
6. Post-edit pipeline; `seat ¬runs(pipeline) ⇒ hand off explicitly`.

Minimal sidecar skeleton — inline so a flat-upload seat needs no map fetch for the core loop:

```markdown
# Compression result — {slug}
**Date:** … · **Executor:** … · **Wave/arc:** …
## Read receipt (fixed overhead — before classify)
| Path | sha256 |
|---|---|
| notes/system/references/skillreducer-research-excerpts.md | … |
| notes/system/references/skill-compression-workflow-map.md | … |
## Classification manifest
| Section | Class | Disposition (L2/L3/delete) |
## Gate-1 checklist
- [ ] Every operational concept from original present in L2 ∪ L3
- [ ] Prior-override bad/good pairs + done-claim gates preserved
- [ ] L1 triggers unchanged unless explicitly scoped
- [ ] Binding URIs + compression-floor list intact
- [ ] Deviations listed (never silent drop)
## Metrics
- Original vs revised lines/chars; approx reduction
## Deviations / flags
```

Batch precedent: agent-bus **4005** (72/72 bundle slugs). Batch dispatch shape + Phase D scripts live in the workflow map; an Anthropic Customize seat hands those off, ¬ invents shell access.

**Compression-floor slugs (minimal touch, operator go required):** `operator-posture`, `completion-provenance-discipline`, `cortex-provenance-discipline`, `git-posture`, `evidence-review-discipline`, session-close provenance slugs.

## Seat tool surface (Anthropic Customize bundle)

Anthropic Customize upload = flat MCP bundle (vortex primaries). **No bash/shell tool.**

| Example class | L2 in bundle | L3 / other seats |
|---|---|---|
| File read/write | `fs(op="read"\|"write"\|"replace", …)` | Same |
| Entity / assert / resolve | `cortex(tool="assert"\|"entity_get"\|"resolve", …)` | Same |
| Bus | `agent_bus(tool="fetch"\|"reply", …)` | Cursor/operator may use CLI equivalents |
| Pipelines | `pipeline(op="run", …)` | Same |
| Services | `manage(action=…)` | Same |
| Git arc integrate | `git_integrate` / `git_land` via overflow/dispatch | Cursor-sdk/operator shell may use raw git |
| Maintenance scripts | ¬ raw `python scripts/…` or fenced bash in L2 | OK in L3 with `seat ∈ {cursor, operator, cursor-sdk}` gate |

Git command strings in anti-pattern tables are vocabulary, ¬ invocation instructions. Compression pass: rewrite L2 bash/scripts examples to MCP tool-call shapes; ¬ add a bash MCP tool to Anthropic Customize.

## Reference consult

`substantive_revise ∨ compression_batch ⇒ consult durable references before rewrite`; ¬ grep-only on known paths. Consult is offline authoring/audit, ¬ runtime skill discovery; runtime discovery = native index + metadata gates.

| Need | Source |
|---|---|
| SkillReducer taxonomy / gates | **Fixed overhead #1** — `fs read` the digest (RAG scope `skill_compilation` distilled into this file; see § Fixed overhead admission gate) |
| Per-slug steps / batch shape | **Fixed overhead #2** — `fs read` the workflow map |
| A live skill's SOT / exemplar | `entity_get` → `source_uri` → `fs read` that path (§ Skill lookup / § SOT authority chain) — file is SOT; entity is pointer + graph |

¬ RAG a **live skill SOT** (stale + disabled). ¬ skip fixed overhead because "the packet has enough corpus" — the digest/map are the SkillReducer method layer on top of domain corpus. Sidecar read receipt is the proof they were surfaced.

Optional live RAG: `rag(op="search", scope="skill_compilation", …)` only to refresh the digest when indexed corpus may have changed — not as a substitute for reading the digest on each pass.

## Authoring checklist

1. Trigger present in `description` + `trigger_match_terms`.
2. Body starts actionable; no "This skill helps you…".
3. Operational rules use FOL where compressible.
4. Line budget follows `agent-guidance-writing`.
5. Decision matrices ≥3 rows use tables.
6. `related_skills` declared in frontmatter or `## Related skills`, ¬ scraped from prose.
7. Registered: entity + README + partition script; file alone is invisible.
8. Re-fetch entity; verify no audit-gate finding fires.

## Post-edit pipeline

```text
edit(SOT) ⇒ entity sync ⇒ [slug ∈ CLAUDE_BUNDLE_SLUGS] bundle check
```

Seat split:

| Seat | Action |
|---|---|
| Anthropic Customize (this bundle) | `fs` write SOT → bus handoff for entity sync + bundle check (no shell) |
| cursor / cursor-sdk / operator | Full pipeline per workflow map Phase D (`ingest_skills.py`, `gen_claude_bundles.py --check`) |

- SOT write target: `workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md` via `fs(op="write"|"replace", path=…)`. The legacy cortex mirror (`agent-skills/{slug}.md`, pending deletion — todo:consolidate-skill-sot) is generated, not authored; ¬ author new skills there. Other historical homes (`docs/agent-guides/rules/{slug}.md`) remain read-only unless explicitly scoped.
- Generated path: `.claude/skills/{slug}/SKILL.md`; edit SOT, never the generated bundle.
- Cursor stub is a thin pointer to SOT unless hardlinked after regen.
- Script-level detail lives in the workflow map Phase D; Anthropic Customize seats hand off that step, ¬ invent shell access.

## Discoverability & lifecycle

- Discoverable set = `lifecycle=active`.
- `draft|deprecated|retired|merged|NULL` ⇒ withheld from `/boot-skills`, `/skills`, `entities`, `/skills/body`.
- `entity_get` marks inactive as `discoverable:false`; `/skills/body` returns `body:null`.
- `include_non_active=true` is a maintenance escape hatch, ¬ a security boundary.
- Out of lifecycle-filter scope: filesystem browse, `cortex(search)`, `/boot-recent-mentions`, `skill_binding` audit detector.
- Graduation requires `lifecycle=active`; `NULL` is silently non-discoverable.

## Guidance class & entity attributes

**Policy (binding):** universal procedure only in skills/rules; matter playbooks outside the guidance index.

```
fs(sandbox="cortex", op="read", path="notes/system/specs/skill-guidance-policy.md")
```

**Split test:** *Would this sentence still be true for a different operator's unrelated matter?* No → case `document:`, not `agent_skill`.

On ingest (`ingest_skills.py`), set entity attributes (schema v1):

| Attribute | Purpose |
|---|---|
| `guidance_class` | `universal_procedure` · `cursor_only` · `matter_retiring` · `retired` |
| `sot_location` | `cortex` · `docs` · `workspace` — where `resolve_sot` finds body |
| `git_policy` | `repo_tracked` · `cortex_mount` · `generated_surface` |
| `export_surfaces` | Which surfaces include slug: `cursor_hardlink`, `claude_bundle`, `claude_ai`, `boot_skills`  |

Static slug lists in `resolver.py` remain build SOT until `--check` consumes attrs (roadmap 2.3–2.5). Do not use attribute-exclusion to keep matter content as `agent_skill`.

## Related skills

- skill-authoring
- frontier-model-instructions
- agent-guidance-writing
