---
name: paste-resolve-cse-followup
description: "Exportable Maestro/Grok finalize recipe after cursor-paste-resolve: cse_session followup with prompt_text (not box paths), HTTP 417 shorten-retry, continuous agent_bus wait over cron bus-watch."
lifecycle: active
trigger_match_terms:
  - paste-resolve followup
  - cse_session followup
  - maestro finalize
  - grok paste-resolve
  - no_prompt followup
  - HTTP 417 followup
  - post-admit bus wait
related_skills:
  - claude-ai-paste-resolve
  - cdp-operator-proxy
  - agent-bus-discipline
---

# Paste-resolve CSE followup (finalize seats)

Genre: thin exportable recipe. Reader = Maestro / Grok (or any host) that **finalizes** after `pipeline_id=cursor-paste-resolve` admits `cursor_sdk` — not the Cursor implementer tab.

Parent entry: Use `claude-ai-paste-resolve` for compose, launch, and watcher arm. This slug covers **optional warm CSE followup** and **closeout watch posture** only.

Evidence: assertion a:38621 (2026-10-06/07 Grok seat); specimen threads in `agent-bus:12286`.

## FOL

- followup ingress ⇔ `cse_session(op=followup, …)` prefer **`prompt_uri`** (`cortex://…`) for large advisories, else **`prompt_text`** (short inline), else **`prompt_path`** only when host-readable (absolute or checkout-relative); box-local `prompt_path` under `/workspace` ⇒ **`no_prompt`** on CDP hosts that cannot read the sandbox (a:38621)
- followup body length ⇔ if project-ask returns **HTTP 417**, halve or cap the body, **retry once**; still 417 ⇒ report transport failure, do not loop (a:38621)
- closeout watch ⇔ **continuous** `agent_bus` wait / `runbook:bus-consult-watcher` re-poll on the cursor-sdk **write thread**; cron-only bus-watch routines ⇒ backup when no attended session can hold a watcher — ¬ default primary (a:38621; `-32001` wait_timeout remains expected ∧ re-poll)
- post-admit paste-resolve ⇔ after `launch_target=cursor_sdk` admit, **write-thread bus wait** is sufficient for implementer closeout; treat failed Glass / local / orion-node / Jupiter launch arms as **noise** unless paste was explicitly required (a:38621)

## Steps

1. **Admit** — parent already fired `cursor-paste-resolve` with watcher armed on each `--dispatch-id` (legs 1–2 per `runbook:bus-consult-watcher`).
2. **Watch** — poll the worker write thread until terminal closeout or designed stop; on MCP `-32001`, re-poll (not a failure).
3. **Optional followup** — when attended CSE must be nudged: `cse_session(op=followup, …)` with `prompt_uri` or short `prompt_text` (+ `registration_id` / `chat_url`) — never unreadable box `prompt_path`.
4. **417 recovery** — one shorten-and-retry on 417; then stop and surface error code in closeout.
5. **Finalize** — parent seat owns `friction_close` / assertion resolution; optional FYI on agent-bus 12286 when `maestro` token was present.

## Refuse

- Using Cowork box paths as `prompt_path` without verifying host read access.
- Replacing continuous watcher with cron-only polling when this session can arm leg 2.
- Chasing Glass/Jupiter launch failures after successful cursor_sdk admit when bus wait already covers closeout.

## Skills

cite `runbook:bus-consult-watcher` · Use `claude-ai-paste-resolve` for the paste-resolve ladder · Use `cdp-operator-proxy` when followup rides operator-proxy CSE lanes.
