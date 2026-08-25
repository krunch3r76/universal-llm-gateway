---
name: session-close-kernel
description: "On operator close triggers — when to close, ceremonious escalation, editorial quality for Arc/decisions/reflections; procedure is seat-routed (life close vs Cursor session_close)."
---

# Session Close — Trigger + Editorial Card

Close only on the operator's close word — never auto-close. Plain "close session" = light depth (default). Verbatim requires an explicit ceremonious qualifier: "full close", "full/verbatim close", "close with handoff", "write a detailed summary". depth=none only when zero decisions ∧ zero entities ∧ no handoff.

## Procedure (seat-routed — pick ONE)

**Do not reconcile mid-close.** Life and Cursor use different verbs; both end in the same journal/transcript commit.

| Seat | Live verb | Do NOT call |
|---|---|---|
| **Life / web-claude** (MCP `close` on tools/list) | `close(op=stage\|draft\|check\|commit)` then optional `close(op=handoff)` | `cortex(tool="session_close")` as the primary path |
| **Cursor IDE** | `cortex(tool="session_close")` after `session_close_preflight` / `doc_validate` per `session-close.mdc` + `/session-end` | `close(op=…)` (not on code-seat tools/list); ¬ `fs(… agent-skills/session-close*.md)` (mirror retired) |

### Life — `close` pipeline

1. `stage` — opens this session's draft; response = known-state + remaining checklist.
2. `draft` — fill fields any time mid-session (summary, decisions, open_items→todos, entity_ids, reflections, handoff, depth). Big payloads via path params, never hand-escaped JSON.
3. `check` — server preflight + audit; remediate until PASS.
4. `commit` — atomic close; idempotent; returns the STOP payload.

Handoff after commit: `close(op=handoff)`.

### Cursor — `session_close` pipeline

1. Load `session-close.mdc` (and `/session-end` when invoked). Depth dial + JSONL Step 0 live there — not in retired `agent-skills/` paths.
2. Resolve JSONL only at `verbatim` (metadata-first `transcript_id`; if harness omits it, one `ls -lt` + hollow gate — ¬ title-grep across dirs).
3. Resolve `session_id`: boot-held `cortex_brief` ID, else `session_close_preflight` with **full required args** (placeholder `summary` + `session_summary_md` OK for ID probe — never ID-only).
4. Compose structural layer → `doc_validate(doc_type="session_close", …)` → `cortex(tool="session_close", …)`.

## Editorial (the part that is genuinely yours)

- `summary` opens with `Arc: <one-line position>` (≤120 chars) — it feeds the next boot **and**, when this close cited a standing root (`entity_ids` / disposition), the open `## Windows` row on that root's charter surface (scoreboard if chartered, continuity-doc otherwise). Fill **after** `session_close` 201 so `journal_row_id` is real. Schema §3.5.
- Decisions are settled claims with the settler named, not narrative.
- Durable follow-ons become `todo:` entities, not prose open items.
- Reflections: write for next-boot-you; register honestly; consolidation only on a real shift.
- Verbatim path only (web): Use the `web-transcript-preprocessing` skill, then attach via `draft(transcript_md_path=…)`. Cursor verbatim: supply `transcript_jsonl_path` only; server assembles.

STOP line: single line, sentinel + real IDs from the commit/`session_close` response only. Never fabricate a conversation_id.
