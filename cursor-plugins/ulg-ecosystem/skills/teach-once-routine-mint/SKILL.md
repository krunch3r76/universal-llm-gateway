---
name: teach-once-routine-mint
description: "After a taught path in a session or bus thread — classify mint-kind (skill or runbook), author, register, attach provenance."
trigger_match_terms:
  - teach-once
  - routine mint
  - save a routine
  - mint skill from session
  - mint runbook
  - taught path
  - show once
  - runbook
related_skills:
  - skill-authoring
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

## Register + attach

**`new_skill` / `skill_revision`:** write catalog SOT (`skill-document-writing`) → census/plugin install if census → `ingest_skills.py` → `entity_get` 200 → `relationship_create(..., type_id=derived_from)` to session/thread/todo. Companions: `related_skills` + `references` (not `requires`). `shared_sync` only: `gen_claude_bundles.py` → jupiter sync.

**`runbook`:** `entity_create(type=runbook)` with `description` = trigger + one-line pointer; `source_uri` = `cortex://notes/runbooks/{slug}.md` (or existing capability card). Write the body. `assert` with `evidence_uris` to the body. Edges to touch-points. One handle — ¬ also mint `document:{slug}-runbook`.

**Standing concern (rare):** genus handle (`work:` / `life:` / `finance:`) + journal via `matter-playbook-lifecycle`. Not the taught-path default.

**Life ceremony (optional):** `agent_bus(tool="substrate_entity_mint")` is a thin `entity_create` wrapper — still needs edges + body. Design-lane dogfood uses `cortex()` + ingest.

## Read-back

Quote `written_sha256` from the SOT write **or** `entity_get` 200. ¬ narrate a content digest.

## Related skills

`skill-authoring` · `skill-document-writing` · `session-close-transcript` · `entity-lifecycle-discipline`
