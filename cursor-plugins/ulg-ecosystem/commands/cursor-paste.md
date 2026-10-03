Open a new Cursor agent and paste a prompt on a named window and host.

Thin wrapper (specimen: `cursor-plugins/ulg-ecosystem/commands/path-sim.md`). Procedure lives in `runbook:cursor-bridge-paste` — ¬ restate env exports, remote env, or the keystroke chord here.

## Invocation

```
/cursor-paste <glass|ide> <orion-node|jupiter> <message-file>
```

| Arg | Allowed | Maps to |
|---|---|---|
| window | `glass` or `ide` | `CURSOR_BRIDGE_WINDOW` |
| host | `orion-node` or `jupiter` | `CURSOR_BRIDGE_SSH_HOST` |
| message-file | existing path | `--message-file` |

Attended Glass is `orion-node`. The launch script's unset-host default is `jupiter`. That default is not this command.

## Refuse

- Either target token missing, or not in its allowed set. Stop and name the two tokens. Do not launch.
- Substituting `jupiter` when the operator said orion, or `glass` when they said IDE.
- Treating `ok: true` as success without quoting `focused.identifier` and the host you exported.

## Steps

1. Parse the three tokens. Write them down before launch.
2. Confirm the message file exists.
3. `cite(runbook:cursor-bridge-paste)` — execute that runbook's steps this call.
4. Report the fields that runbook names: `ok`, `keystroke.steps`, `focused.title`, `focused.identifier`, exported host.

## Skills

cite `runbook:cursor-bridge-paste`
