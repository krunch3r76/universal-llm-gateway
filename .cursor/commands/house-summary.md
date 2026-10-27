# /house-summary [&lt;thread_id&gt;] [last N hours]

**LOAD AND EXECUTE:** `runbook:house-summary`  
Body: `cortex://notes/runbooks/house-summary.md` (`fs` sandbox `cortex`, or `cortex(entity_get, entity_id=runbook:house-summary)`).

Produce a **browser-open HTML** summary of an agent-bus **continuity house** (root thread with `role:root` / liaison lane). Emphasis is **so what** — operator impact first; bus turn laundry belongs in a collapsed appendix.

## Usage

| Operator says | Action |
|---|---|
| `/house-summary 20607` | Summarize house thread **20607**, default **24h** window |
| `summarize house 20607` | Same — cite and execute `runbook:house-summary` |
| `last 48 hours on house 20607` | Pass `window_hours=48` per runbook § Parameters |

## Output

Writes (by default):

`/mnt/torus/projects/universal-llm-gateway/tmp/<thread_id>-house-summary.html`

Reply with the `file://` path. Overwrites on refresh.

## Not this command

- **`/session-end`** — session close pipeline, not house activity digest.
- **`runbook:house-close`** — closes lineage threads, not summary.
- **Dispatch** — summary is in-seat read + HTML write only (`agent_bus_read`, `cortex` search, `fs` write to `tmp/`).
