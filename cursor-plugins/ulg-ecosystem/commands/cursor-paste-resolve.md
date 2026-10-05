Open a Cursor agent to resolve one friction or assertion.

Thin wrapper (specimen: `cursor-plugins/ulg-ecosystem/commands/path-sim.md`). Procedure lives in `runbook:cursor-paste-resolve`. Compose is `pipeline_id=cursor-paste-resolve`. Work-prompt SOT stays `cortex://notes/system/prompts/work-item-implementer-friction.md` — ¬ a skill. ¬ restate maestro paragraph bodies or bridge exports here.

## Invocation

```
/cursor-paste-resolve <friction|assertion> <id> [<glass|ide>] [<orion-node|jupiter>] [maestro] [cursor_sdk] [opus|fable|…] [tab-opus|tab-fable]
```

After kind and id, zero or more tokens. Classify each by its set. `cursor_sdk` sets `launch_target=cursor_sdk` (no GUI paste). `opus` / `fable` (and aliases) set `densify` — they do not imply `launch_target=cursor_sdk`. `tab-opus` / `tab-fable` set Glass Ctrl+/ query only.

| Arg | Allowed | When omitted |
|---|---|---|
| kind | `friction` or `assertion` | required |
| id | positive integer | required |
| window | `glass` or `ide` | `glass` |
| host | `orion-node` or `jupiter` | the one host this request's context names |
| notify | `maestro` | no maestro memo — closed title suffix is `complete` |
| launch_target | `cursor_sdk` in this set | window (Glass/IDE paste) |
| densify | `opus`, `claude-opus`, `cursor/claude-opus-5-5`, `fable`, `claude-fable`, `cursor/claude-fable-5-1`, `cursor/claude-fable-5` | no densify hop |
| tab | `tab-opus`, `tab-fable` | no Glass model query |

Fold densify aliases to `opus` or `fable` before calling the pipeline. Fold tab tokens to `tab_model=opus|fable`. A missing window focuses Cursor Glass. A missing host is never filled with `jupiter` or `orion-node` by default. Thread `12286` is the maestro closure memo only — never `dispatch_thread_id`.

## Refuse

- Kind missing or not in its allowed set. Stop and name the two kinds.
- Id missing or not a positive integer.
- A target token in neither set, or two tokens in the same set. Do not launch.
- Host omitted and this request's context does not name exactly one of `orion-node` or `jupiter` (Glass/IDE). Stop and name the two hosts.
- `assertion_get` does not return that id. Do not launch.

## Steps

1. Parse kind, id, window, host, notify, `launch_target`, densify, tab. An unresolved Glass/IDE host is a stop.
2. `cite(runbook:cursor-paste-resolve)` — execute that runbook. Prefer `pipeline(op=run, pipeline_id=cursor-paste-resolve, options={kind, assertion_id, window, host, notify, launch_target, densify, tab_model})` for compose (and launch when `launch_target` is set). The pipeline does not call this command.
3. If the pipeline stopped after the message file, cite `runbook:cursor-bridge-paste` for Glass/IDE.
4. Report kind, id, window, host, maestro, `launch_target`, densify, tab_model, host source (explicit vs context), plus paste or admit fields from the runbook.

## Skills

cite `runbook:cursor-paste-resolve` · cite `runbook:cursor-bridge-paste` when Glass/IDE paste is the second step
