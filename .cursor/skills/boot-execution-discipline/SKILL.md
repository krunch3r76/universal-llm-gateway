---
trigger_match_terms: ["boot-execution-discipline", "boot_execution_discipline", "cortex", "boot", "continue-transcript", "write-recovery", "session-boot-close", "continue-from-transcript", "interrupted-write", "recovery", "boot-facing", "artifact"]
description: 'On any Cortex boot, continue-from-transcript, interrupted-write recovery, or boot-facing artifact update: use a write ledger, perform writes, verify by readback, then report exact paths/entity IDs.'
---

# Boot Execution Discipline

**Version:** 2.0-compressed  
**Authority:** HIGH — every Cortex boot, continue-from-transcript, interrupted-write recovery, or boot-facing artifact update.

Workspace counterpart to completion/provenance posture rules: posture says completion claims require observed payloads; this skill gives write ledger, readback, and boot artifact mechanics.

## Trigger

Read before first substantive answer when:
- user says “boot up,” “continue from transcript:<id>,” “continue prior session,” or similar Cortex-continuity language;
- task touches Cortex state, case files, document indexes, agent skills, or session continuity;
- prior write path failed/interrupted and user asks “did you drop?”, “what happened?”, “proceed,” or resume exact write;
- task updates a boot-facing artifact: case file, document index, discipline skill, README/manifest, agent-skill entity.

Model tier does not replace write→verify→report discipline.

## Core rule

Do not report completion until verified by readback/entity fetch or an atomic write payload. Correct order:
1. Identify required writes.
2. Perform writes.
3. Verify with appropriate readback.
4. Report exact paths/entity IDs/assertion IDs and verification source.

Completion claim without artifact identifier + verification source is speculation.

## Boot precedence ladder

At Cortex boot:
1. Fresh user direction now.
2. Explicit boot substrate/instructions.
3. Primary source / transcript / case file / document index.
4. Cortex card / boot summary / handoff prose — orientation only.

If user names transcript ID, treat as source pointer. If user corrects a fact, verify against Cortex where possible, then update record rather than arguing from memory.

Override: boot-specified artifact locations override remembered layouts, inferred conventions, and session carryover until direct read/list proves otherwise.

Capability claims in handoff prose are advisory and can lag the live manifest. Confirm capability once with `tool_search` at boot, then proceed; do not re-verify resolved tools repeatedly. Durable web-anthropic lead-seat surface: `reference:claude-web-lead-seat-surface`; full census: `cortex://notes/system/threads/claude-web-lead-seat-surface-manifest.md`.

## Mandatory write ledger

Before mutating Cortex/files, keep an internal row per intended mutation:

| Mutation | Write | Verification | Report |
|---|---|---|---|
| Case file section | `fs(write/replace/md_replace)` | `fs(read/md_read)` | path + section |
| Document index | `fs(write/replace/md_replace)` | `fs(read/md_read)` + optional entity hash refresh | path + section |
| Skill file | `fs(write)` | `fs(read)` | skill path |
| Skill entity | `cortex(entity_create/update)` | `cortex(entity_get)` | entity ID |
| Assertion/relationship | `cortex(assert/relationship_create)` | returned ID + `entity_get`/`assertions` | assertion/relationship ID |

Final response must say what was written, where, and how verified. Ledger need not be shown unless useful.

## Verification standards

### Files
- Small file: `fs(read)`.
- Markdown >~5k chars: `fs(md_list)` + `fs(md_read)`.
- Section edit: verify edited section, not file existence.
- Canonical source named by entity: if content-hash freshness matters, `entity_update(source_uri=<same path>)`, then `entity_get`.

### Cortex graph
- Capture returned IDs.
- Fetch target entity after material update.
- Do not call an assertion canonical unless visible on entity or you have write response ID.
- If assertion is staged/flagged, report that status; do not call it committed.

### Agent skills
Skill addition complete iff all true:
1. `agent-skills/<slug>.md` exists and reads back.
2. `agent-skills/README.md` includes skill row.
3. `agent_skill:<slug>` exists with `source_uri` to skill file.

If any fail, report partial state and stop.

## Case boot/source discipline

For case-tracked matters, read canonical document index before path claims or index updates. Never infer case artifact path from directory memory if index exists.

Chase escrow anchors:
- skill: `matter-playbook-lifecycle` (entities.md + journal.md);
- evidence skill: `case-evidence-retrieval`;
- index entity: `document:chase-escrow-document-index`;
- index file: `documents/finance/case-chase-mortgage-escrow-2026/chase-escrow-document-index.md`;
- case file: `documents/finance/case-chase-mortgage-escrow-2026/case-file.md`.

## Interruption recovery

After timeout/drop, classify before proceeding:
- **Confirmed done:** tool result observed and verified.
- **Attempted but unverified:** write tool called but no readback.
- **Not done:** no write result observed.

Do not blame MCP/model/transport unless a tool error supports it. If tools exist now, default diagnosis for “planned but did not write” is execution-discipline failure.

## Failure canaries

Halt and re-enter ledger if about to say without readback:
- “Done/completed/updated” with no path/section/entity ID.
- “I created the entity” without entity ID + `entity_get` verification.
- “The boot now shows…” without naming updated boot-facing file/entity.
- “Maybe MCP is unwired” before checking tool availability and errors.
- “I will proceed” followed by narrative, not tool calls.
- “The record says…” from boot summary/memory when case file/transcript/index exists.

## Final report template

After verified execution:

```text
Completed and verified.

Updated:
- <path/entity/assertion> — <what changed>

Verification:
- <readback/tool/entity_get result>

Caveats:
- <staged/provisional/missing/unverified only>
```

If nothing was written, do not use template; state blocker plainly.

## Session greeting — web-anthropic tentative

If `cortex_brief` ran on web-anthropic lead seat, first visible line should be:

```text
**Session:** `web-anthropic-YYYY-MM-DD-HHMM`
```

Omit if boot skipped; never fabricate. Scope is web-anthropic unless another seat adopts it.

## Related

`completion-provenance-discipline` / `provenance-discipline.mdc` (payload-bound completion posture) · `case-evidence-retrieval` · `engagement-stance`
