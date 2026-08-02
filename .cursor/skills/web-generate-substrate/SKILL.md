---
name: web-generate-substrate
description: On any web-auto, web-generate, claude/web-auto, or WEB_AUTOMATION_MCP_TOKEN task — read this index before acting. Pointer only; web-auto is a Track 1 proposal (P0 gate green, P1+ not landed).
trigger_match_terms: ["web-generate-substrate", "web_generate_substrate", "web-auto", "web-generate", "claude/web-auto", "WEB_AUTOMATION_MCP_TOKEN"]
related_skills: ["consult-routing"]
---

# web-generate-substrate

`task ∈ {web-auto, web-generate, claude/web-auto, WEB_AUTOMATION_MCP_TOKEN}` ⇒ load this index before acting.

Status: **Track 1 proposal only**. P0 gate is green (thread 1600); P1+ has not landed. Until `claude/web-auto` ships, use manual `web-consult` / `web-implement` handoff + operator push.

## Pointers

- Deck: `tmp/prompts/web-generate-substrate/README.md` — phase manifest §3, locked decisions §4, operator next steps §12.
- P0 smoke: `scripts/web-automation-substrate-smoke.py` — headless vortex MCP auth + bus closeout; env `WEB_AUTOMATION_MCP_TOKEN`, `VORTEX_MCP_URL`.
- Routing: skill `consult-routing` § Dispatch targets → web-auto row.
- Work item: `todo:web-generate-substrate`.
