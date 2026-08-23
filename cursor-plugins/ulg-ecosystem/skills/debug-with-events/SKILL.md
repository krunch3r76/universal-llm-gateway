---
name: debug-with-events
description: When debugging opaque/silent failures in gateway workers or pipeline execution — instrument with debug events and query Event Service (operational query SoT) before reaching for application logs.
trigger_match_terms: ["debug-with-events", "debug_with_events", "debug", "silent", "gateway-worker", "failure", "pipelines-rag-mcp", "opaque", "failures", "gateway", "workers", "instrumenting"]
---

# Debug With Events

**SOT:** `workspaces://universal-llm-gateway/.cursor/skills/debug-with-events/SKILL.md`
(event reference: `workspaces://universal-llm-gateway/.cursor/skills/debug-with-events/event-reference.md`)

## Trigger

*Imperative: When debugging opaque/silent failures in gateway workers or pipeline
execution — instrument with debug events and query Event Service (operational query
SoT) before reaching for application logs.*

```
fs(sandbox="workspaces", op="read",
   path="universal-llm-gateway/.cursor/skills/debug-with-events/SKILL.md")
```

Pairs with `[ulg:events-first]` (`ulg-architecture` skill) and the event-debugging rule
`workspaces://universal-llm-gateway/.cursor/rules/event-debugging_ws.mdc`.
Do not maintain a second long-form copy here — cortex is the discovery index only.
