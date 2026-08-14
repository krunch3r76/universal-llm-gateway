---
name: teach-once-routine-mint
description: "After a taught path in a session transcript or bus thread — extract excerpt, classify mint-kind, author skill or matter playbook, register, attach provenance."
trigger_match_terms: ["teach-once", "routine mint", "save a routine", "mint skill from session", "taught path", "show once"]
related_skills: ["skill-authoring", "skill-document-writing", "session-close-transcript", "matter-playbook-lifecycle"]
---

# Teach-once routine mint

`taught_path ⇒ extract ∧ classify ∧ author ∧ register ∧ attach` — Bot must not own the routine off-graph.

```
teach_once := seat_followed_playbook
¬ coded_JSONL_parser  ¬ whole_session_dump  ¬ imprint(agent_skill)
```

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

Split test: *Would this sentence still be true for a different operator's unrelated matter?*

| Result | `mint_kind` |
|---|---|
| Yes ∧ no incumbent owns the neighborhood | `new_skill` → `agent_skill:{slug}` |
| Yes ∧ incumbent owns it | `skill_revision` → `CANDIDATE SKILL REVISION` then apply (`skill-document-writing`) |
| No | `matter_playbook` → `document:` + matter-playbook-lifecycle (¬ `has_playbook`) |

`new_skill` without a neighborhood scan is a defect. `imprint` cannot mint `agent_skill`.

## Register + attach (code lane)

**`new_skill` / `skill_revision`:** write catalog SOT (`skill-document-writing`) → census/plugin install if census → `ingest_skills.py` → `entity_get` 200 → `relationship_create(..., type_id=derived_from)` to session/thread/todo. Companions: `related_skills` + `references` (not `requires`). `shared_sync` only: `gen_claude_bundles.py` → jupiter sync.

**`matter_playbook`:** matter-playbook-lifecycle MINT/WIRE/RING/AUTHOR.

**Life ceremony (optional):** `agent_bus(tool="substrate_entity_mint")` is a thin `entity_create` wrapper — still needs edges + SKILL.md. Design-lane dogfood uses `cortex()` + ingest.

## Read-back

Quote `written_sha256` from the SOT write **or** `entity_get` 200. ¬ narrate a content digest.

## Related skills

`skill-authoring` · `skill-document-writing` · `session-close-transcript` · `matter-playbook-lifecycle`
