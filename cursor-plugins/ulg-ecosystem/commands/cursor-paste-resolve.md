Open a Cursor agent to resolve one friction or assertion.

Thin wrapper (specimen: `cursor-plugins/ulg-ecosystem/commands/path-sim.md`). Procedure lives in `runbook:cursor-paste-resolve`. **Compose and launch are `pipeline_id=cursor-paste-resolve`** — not in-seat file assembly. Work-prompt SOT stays `cortex://notes/system/prompts/work-item-implementer-friction.md` — ¬ an `agent_skill`. ¬ restate maestro paragraph bodies or bridge exports here.

**Glass/IDE keyboard paste is still supported**, via the pipeline. Default `launch_target` is the window (`glass` when omitted). The launch step shells `scripts/cursor-bridge-launch.py open-tab` (same chord as `runbook:cursor-bridge-paste`). Investigate `opus`/`fable` is a prior cursor-sdk hop — it is not Glass launch and does not steal paste. Do not call that hop densify (work-item densify is a different process).

## Invocation

```
/cursor-paste-resolve <friction|assertion> <id> [<glass|ide>] [<orion-node|jupiter>] [maestro] [cursor_sdk] [no-paste|sdk-write|admit] [opus|fable|…] [tab-opus|tab-fable]
```

After kind and id, zero or more tokens. Classify each by its set. `opus` / `fable` (and aliases) set `investigate` — they do not imply `launch_target=cursor_sdk`. `tab-opus` / `tab-fable` set Glass Ctrl+/ query only.

**Attachment.** `cursor_sdk` is SDK write only when it is the launch flag. Next to an investigate token it names the investigate substrate (already cursor-sdk). A named window keeps paste unless a steal cue is present (`no-paste` / `sdk-write` / `admit` → `launch_target=cursor_sdk`). `cursor_sdk` next to an investigate token does not imply launch when a window token is also present.

| Arg | Allowed | When omitted |
|---|---|---|
| kind | `friction` or `assertion` | required |
| id | positive integer | required |
| window | `glass` or `ide` | `glass` |
| host | `orion-node` or `jupiter` | the one host this request's context names |
| notify | `maestro` | no maestro memo — closed title suffix is `complete` |
| launch_target | steal cues; bare `cursor_sdk` only when no window, or with investigate peeled | window (Glass/IDE paste) |
| investigate | `opus`, `claude-opus`, `cursor/claude-opus-5-5`, `fable`, `claude-fable`, `cursor/claude-fable-5-1`, `cursor/claude-fable-5` | no investigate hop |
| tab | `tab-opus`, `tab-fable` | no Glass model query |

`finalize` is options-only (no slash token in v1): default `dispatchee` (the executor restarts and closes, as today). `finalize=dispatcher` appends the dispatcher-finalize block last (executor lands, posts land_sha + importer services, stops) and refuses on `glass`/`ide`; Cowork passes it on every cursor_sdk fire (a:38698).

Fold investigate aliases to `opus` or `fable` before calling the pipeline. Fold tab tokens to `tab_model=opus|fable`. Fold steal cues to `launch_target=cursor_sdk`. A missing window focuses Cursor Glass. A missing host is never filled with `jupiter` or `orion-node` by default. Thread `12286` is the maestro closure memo only — never `dispatch_thread_id`.

Specimens: `glass orion-node opus cursor_sdk` (or “Glass … Opus on cursor_sdk”) → window=glass, investigate=opus, **paste Glass**. `cursor_sdk opus` (no window) → investigate=opus, **SDK write**. `glass orion-node opus no-paste` → investigate=opus, **SDK write**.

## Refuse

- Kind missing or not in its allowed set. Stop and name the two kinds.
- Id missing or not a positive integer.
- A target token in neither set, or two tokens in the same set. Do not launch.
- Window + bare `cursor_sdk` without investigate and without a steal cue. Stop; name paste (omit `cursor_sdk`) vs admit (`no-paste`).
- Host omitted and this request's context does not name exactly one of `orion-node` or `jupiter` (Glass/IDE). Stop and name the two hosts.
- `assertion_get` does not return that id. Do not launch.

## Steps

1. Parse kind, id, window, host, notify, investigate, tab; apply attachment for `launch_target`. An unresolved Glass/IDE host is a stop.
2. `cite(runbook:cursor-paste-resolve)` — execute that runbook. Fire `pipeline(pipeline_id=cursor-paste-resolve, options={kind, assertion_id, window, host, notify, launch_target, investigate, tab_model})` for compose **and** launch. Use `op=async` when investigate or `launch_target=cursor_sdk` will outlive this turn. The pipeline does not call this command.
3. **Watcher (binding).** Every cursor-sdk hop this command causes — investigate, and `launch_target=cursor_sdk` write — needs `runbook:bus-consult-watcher` on that `--dispatch-id` before this turn exits (legs 1–2). Pipeline wait / `op=run` is not the watcher. Investigate admit ≠ Glass paste.
4. If the pipeline returned compose-only (`launch_target` empty), cite `runbook:cursor-bridge-paste` for Glass/IDE. When `launch_target=glass|ide`, the pipeline already ran the keystroke paste.
5. Report kind, id, window, host, maestro, `launch_target`, investigate, tab_model, host source (explicit vs context), paste or admit fields from the runbook, and each cursor_sdk `dispatch_id` + watcher label.

## Skills

This command has **no** `agent_skill`. cite `runbook:cursor-paste-resolve` · cite `runbook:bus-consult-watcher` on every cursor_sdk hop · cite `runbook:cursor-bridge-paste` only when Glass/IDE paste is a leftover compose-only second step · Maestro/Grok finalize after admit: Use `paste-resolve-cse-followup` (a:38621).
