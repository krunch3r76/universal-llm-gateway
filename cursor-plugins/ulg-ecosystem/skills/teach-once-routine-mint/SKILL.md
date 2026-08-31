---
name: teach-once-routine-mint
description: "After a taught path, or when authoring a runbook body — classify mint-kind, write a lean invoked command (trigger/refuse/steps/falsifier), register, attach provenance."
trigger_match_terms:
  - teach-once
  - routine mint
  - save a routine
  - mint skill from session
  - mint runbook
  - write a runbook
  - runbook body
  - taught path
  - show once
  - runbook
related_skills:
  - skill-document-writing
  - session-close-transcript
  - entity-lifecycle-discipline
---

# Teach-once routine mint

`taught_path ⇒ extract ∧ classify ∧ author ∧ register ∧ attach` — Bot must not own the routine off-graph.

```
teach_once := seat_followed_playbook
¬ coded_JSONL_parser  ¬ whole_session_dump  ¬ imprint(agent_skill)
```

SOT for the invoked-command slot: `decision:runbook-as-cortex-command`.

## FOL pipeline

```
select(source) ≺ extract(excerpt) ≺ split_test(mint_kind) ≺ neighborhood_check
≺ author(body) ≺ register ≺ attach(provenance) ≺ read_back
```

## Sources (pick one primary)

| # | Source | When |
|---|---|---|
| 1 | `session_close` `transcript_md` | Close already ran |
| 2 | Agent-transcript JSONL via `transcript_assembly.resolve_jsonl_path` | Close has not run; prefer assembled md |
| 3 | Named agent-bus thread | Taught path was bus-native |

## Extract (into the minted body)

1. Quoted teaching utterance.
2. Reusable procedure (FOL or numbered steps).
3. Falsifier / anti-pattern.
4. Provenance URIs (session, bus turn, assertion ids).

## Mint-kind

Split test: *Would this sentence still be true for a different operator's unrelated work?*

| Result | `mint_kind` |
|---|---|
| Yes ∧ no incumbent owns the neighborhood | `new_skill` → `agent_skill:{slug}` + SKILL.md |
| Yes ∧ incumbent owns it | `skill_revision` → `CANDIDATE SKILL REVISION` then apply (`skill-document-writing`) |
| No ∧ invoked (trigger phrase / "do this") | `runbook` → `runbook:{slug}` + note body |
| No ∧ standing concern (rare from a taught path) | genus handle + journal — not the default |

`new_skill` without a neighborhood scan is a defect. `imprint` cannot mint `agent_skill`. `matter_playbook` is not a mint-kind.

`agent_skill:` is metadata on a loaded skill. The procedure lives in the SKILL.md (or inject/path). `runbook:` is the invoked command — cortex only; do not mirror into `.cursor/commands/`.

## Author runbook body

Borrow **writing** principles from `skill-document-writing` (trigger precision, refuse, lean always-loaded procedure, no rule-in-example). Do **not** run SkillReducer fixed overhead, ingest, plugin install, or Customize upload — those are skill-slot machinery.

House specimens (`extraordinary-aperture`, `{N}-house`) already show the shape. This section is so the next mint does not grow an 11-section restatement of three skills.

```
runbook_body =
  trigger ≺ refuse ≺ numbered_steps_this_call ≺ falsifier ≺ provenance
law_stays_in(skill)  ∧  runbook_points  ∧  ¬restack
```

| Keep in the body | Leave in the skill / L3 |
|---|---|
| Precise trigger (same duty as skill L1 `description`) | Background, teaching story, rationale |
| Refuse table (prior-override bad/good) | Full skill restatement |
| Numbered steps the seat does *this invocation* | Arg catalogs, op tables, CHECKPOINT field IDs |
| One falsifier | Happy-path examples unless they override a prior miss |
| Provenance URIs | A twin `document:{slug}-runbook` |

**Rule-in-example:** if “don’t `post` on a live thread” only lives in a story, promote it to Refuse or a numbered step — same Gate-2 miss as skills.

**Density:** bloated body ⇒ seats skip it. Prefer six-or-so steps + pointers over a section per composing SOT.

YAML frontmatter is convention, not a validator: `runbook:`, aliases, mint, provenance. Card shape stays § Register (`description` = trigger + one-line pointer; steps stay in the note).

## Register + attach

**`new_skill` / `skill_revision`:** write catalog SOT (`skill-document-writing`) → census/plugin install if census → `ingest_skills.py` → `entity_get` 200 → `relationship_create(..., type_id=derived_from)` to session/thread/todo. Companions: `related_skills` + `references` (not `requires`). `shared_sync` only: `gen_claude_bundles.py` → jupiter sync.

**`runbook`:** `entity_create(type=runbook)` with `description` = trigger + one-line pointer; `source_uri` = `cortex://notes/runbooks/{slug}.md` (or existing capability card). Write the body. `assert` with `evidence_uris` to the body. Edges to touch-points. One handle — ¬ also mint `document:{slug}-runbook`.

**Standing concern (rare):** genus handle (`work:` / `life:` / `finance:`) + journal via `matter-playbook-lifecycle`. Not the taught-path default.

**Life ceremony (optional):** `agent_bus(tool="substrate_entity_mint")` is a thin `entity_create` wrapper — still needs edges + body. Design-lane dogfood uses `cortex()` + ingest.

## Read-back

Quote `written_sha256` from the SOT write **or** `entity_get` 200. ¬ narrate a content digest.

## Anti-patterns

| Bad | Good |
|---|---|
| 11-section runbook that restates three skills | Steps this call + pointers |
| SkillReducer digest/map before every runbook mint | Writing principles only |
| Rule that only lives in a teaching story | Refuse or a numbered step |
| `document:{slug}-runbook` twin | One `runbook:` handle |

## Related skills

`skill-document-writing` · `session-close-transcript` · `entity-lifecycle-discipline`
