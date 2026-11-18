Open a new Cursor agent and paste a prompt on a named window and host.

Thin wrapper (specimen: `cursor-plugins/ulg-ecosystem/commands/path-sim.md`). Procedure lives in `runbook:cursor-bridge-paste` — ¬ restate env exports, remote env, or the keystroke chord here.

**Policy (2026):** Glass automation from MCP is **CDP-only** — `cursor_bridge` `spawn_tab` / `wake_tab` with `gui_host=jupiter`, `orion-node`, or `:8`. Use this command only as an **operator fallback** (uinput paste) when CDP is down. The `ide` window mode is **deprecated** for fleet control.

## Invocation

```
/cursor-paste <glass> <jupiter|orion-node> <message-file>
```

| Arg | Allowed | Maps to |
|---|---|---|
| window | `glass` only | `CURSOR_BRIDGE_WINDOW=glass` |
| host | `jupiter` (Cosmic attended) or `orion-node` | `CURSOR_BRIDGE_SSH_HOST` |
| message-file | existing path | `--message-file` |

Attended Cosmic Glass on fleet **jupiter** → CDP hub port **19339** on **io** (tunnel to jupiter **19338**). **orion-node** uses hub **19338**.

## Refuse

- Either target token missing, or not in its allowed set. Stop and name the two tokens. Do not launch.
- Substituting hosts or `ide` when the operator named glass+jupiter.
- Treating `ok: true` as success without quoting `focused.identifier` and the host you exported.

## Steps

1. Parse the three tokens. Write them down before launch.
2. Confirm the message file exists.
3. `cite(runbook:cursor-bridge-paste)` — execute that runbook's steps this call.
4. Report the fields that runbook names: `ok`, `keystroke.steps`, `focused.title`, `focused.identifier`, exported host.

## Skills

cite `runbook:cursor-bridge-paste`