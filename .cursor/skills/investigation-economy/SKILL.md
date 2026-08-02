---
trigger_match_terms: ["investigation-economy", "investigation_economy", "read-class", "observation", "voi", "gate", "review-reasoning", "cross-cutting", "behavioral", "discipline", "fires", "instant"]
---

# Investigation Economy

In-the-moment gate for every read-class observation.

## Trigger

Before ANY read-class observation — `fs(read/list)`, source dive, re-poll (`build_status`, pipeline result), `entity_get`, bus fetch, or “let me check/confirm” call — run this gate. The instant before the call is the trigger.

## VOI gate

`observation_allowed ⇔ ∃ possible_result : result changes next_action`.

Plain English: **Would any outcome of this observation change my next action?** If no, do not observe.

Over-investigation is usually comfort-seeking, not information-seeking: the action is already determined, but the agent reads to reduce discomfort.

## VOI=0 variants

- **Redundant:** outcome already implied by signals in hand. `two_cheap_signals_agree ⇒ act ∧ ¬source_read_to_confirm`.
- **Over-broad:** observed scope exceeds decision need. Read the narrowest useful slice.
- **Repeated:** signal has not changed since prior look, or changed value would not alter action. Stop polling; go to ground truth once if needed.

## Exceptions: read when value is real

Read when an outcome would change the action, especially:

1. **Irreversible/costly action:** merge, send, money move, delete, live-config change. Low probability × high cost = high VOI.
2. **Conflicting cheap signals:** status says X, diff says Y; read to resolve.
3. **First use/no prior:** confirm a tool/path semantics once. After capability is established, re-confirming becomes redundant.

## Width gate

If a read clears VOI, read the narrowest useful slice first; widen only if it fails.

- **agent_bus:** `fetch(compact=true)` / thread digest → `get(thread, turn_number)` for one body.
- **cortex:** summary/list/search with small `limit ≤ 7`; `entity_get(intent="card")` before full; deepen one row with `assertion_get` when needed.
- **fs:** for large docs/sidecars, `md_list`/`md_read(section)` or `read(offset, limit)`, not whole-file read.

`oversized_pull ⇒ session_context_inflation`; compact-first surfaces exist to prevent this.

## Session-close falsifier

Ask at close: **Did I issue any read/list/poll/source-dive whose outcome could not have changed my next action? List each. Target: 0.**

Transcript signature: a read whose result is not cited in the subsequent decision rationale is inert. Advisory/WARN only; never block reads globally.

## Escalation falsifier

If inert-read count does not trend toward 0 across sessions, written discipline failed. Escalate structurally: pre-read interstitial, higher read friction, or session-close audit WARN detector. Do not add another ignored paragraph.

## AwaitShell / long Shell (Cursor harness)

`long_shell_job ⇒ background (block_until_ms:0) ∨ short smoke; ¬ turn-holding AwaitShell poll`.

| Pattern | Verdict |
|---|---|
| Local 70B / multi-minute `curl` to `:9999` while `AwaitShell` blocks the turn | **Forbidden** — background; continue other work or end turn |
| `block_until_ms` ≥ 60s used as a substitute for backgrounding | **Forbidden** unless the very next step is blocked on that exit |
| Smoke-check once after `block_until_ms:0` spawn | Allowed |
| Close-monitoring loop on hung trainings/deploys | Allowed only per harness close-monitoring rules |

Presence-discipline **P4** is the always-on stub. Structural hard-stop (harness cap / auto-background) is the escalation — do not keep adding prose alone.

## Landing

- Reflective-journal / boot-surfaced falsifier = primary push surface.
- This skill = canonical text, pull-only reference.
- Deferred structural work: `todo:investigation-economy-structural-enforcement` — inline one-line gate into operational context and add `over_investigation` close-audit WARN.
