---
name: claude-ai-paste-resolve
description: "Thin Cowork wrapper: friction or assertion → runbook cursor-paste-resolve + pipeline cursor-paste-resolve; default launch_target cursor_sdk; this seat finalizes. ¬ Grok liaison."
lifecycle: active
trigger_match_terms:
  - claude-ai-paste-resolve
  - paste-resolve
  - cursor-paste-resolve
  - friction resolve
  - assertion resolve
  - cursor_sdk
  - maestro
  - Cowork
  - web-anthropic
  - claude-web
related_skills:
  - cdp-operator-proxy
  - lean-dispatch
  - agent-bus-discipline
  - completion-provenance-discipline
---

# Claude.ai paste-resolve

Genre: thin Cowork / web-anthropic (claude-web) wrapper. Reader = frontier model on Claude.ai.

**SOT:** cite `runbook:cursor-paste-resolve`. Compose + launch = `pipeline_id=cursor-paste-resolve` on the connector that exposes pipeline (usually code/user-code). Work-prompt SOT stays `cortex://notes/system/prompts/work-item-implementer-friction.md`. ¬ agent_skill fork of Cursor command body.

Cursor specimen: `cursor-plugins/ulg-ecosystem/commands/cursor-paste-resolve.md`.

This is **not** the Grok liaison path-1 peer-spawn skill — Cowork **is** the dispatchee and finalizes.

## FOL

- seat ⇔ web-anthropic ∨ claude-web ∨ Cowork
- default_launch ⇔ cursor_sdk
- `launch_target=cursor_sdk` ⇔ `team_dispatch(seat=cursor-sdk)` via pipeline
- future_launch ∈ {cursor_bridge} ⇔ Glass/IDE paste — opt-in only when wired; ¬ default today
- ¬ grok_bot_bridge
- friction_resolve ⇒ judgment_here ∨ investigate_opt_in ∧ pipeline ∧ finalize_here
- finalize ⇔ this_seat owns land + friction_close (same as spawned Cursor tab)
- notify=maestro ⇔ closure memo thread **12286** only — never `dispatch_thread_id`
- wait_timeout MCP -32001 ⇔ expected ∧ re-poll agent_bus
- ¬ invent wires/runbook steps

## Invocation

```
/claude-ai-paste-resolve <friction|assertion> <id> [maestro] [cursor_sdk|sdk-write|no-paste|admit] [opus|fable] [cursor_bridge] [glass|ide] [orion-node|jupiter]
```

Default `launch_target=cursor_sdk`. Refuse missing kind/id; unknown token; inventing bridge before runbook supports Cowork→bridge.

## Refuse

- Kind missing or not `friction` / `assertion`.
- Id missing or not a positive integer.
- Token in none of the allowed sets, or two tokens in the same set.
- `cursor_bridge` / Glass / IDE launch when runbook does not yet wire Cowork→bridge (document only — do not enable by default).

## Steps

1. **Parse** kind, id, notify, launch_target, investigate, optional future bridge/window/host tokens per runbook attachment rules.
2. **Judgment** (or investigate when `opus` / `fable` opt-in) — bind before pipeline fire.
3. **cite(runbook:cursor-paste-resolve)** — execute; do not restate runbook bodies here.
4. **Pipeline** — `pipeline(pipeline_id=cursor-paste-resolve, options={…})` compose + launch; use async when investigate or `launch_target=cursor_sdk` outlives this turn.
5. **agent_bus watch** — every cursor-sdk hop gets watcher per runbook; on MCP `-32001` wait_timeout, re-poll (expected).
6. **Finalize** — `friction_close` when acceptance criteria met; maestro memo only on thread 12286 when `maestro` token present.

**Admit model (non-investigate sdk write):** `cursor/composer-2.5` (prefer code over stale runbook prose).

## Skills

This slug is the Cowork entry. ¬ duplicate `cursor-paste-resolve` command body. cite `runbook:cursor-paste-resolve` · cite `runbook:bus-consult-watcher` on every cursor_sdk hop · `cursor_bridge` / `runbook:cursor-bridge-paste` only when explicitly opted in and runbook-backed.
