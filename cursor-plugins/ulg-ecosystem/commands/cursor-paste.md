Open a new Cursor agent and paste a prompt on a named window and host.

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

1. Parse the three tokens from the invocation. Write them down before the shell call.
2. Confirm the message file exists. It is the full prompt, including any rename line.
3. From the repo root, with the universal venv:

```bash
export CURSOR_BRIDGE_SSH_HOST=<orion-node|jupiter>
export CURSOR_BRIDGE_WINDOW=<glass|ide>
export CURSOR_BRIDGE_REMOTE_ENV="WAYLAND_DISPLAY=wayland-1 XDG_RUNTIME_DIR=/run/user/1000 CURSOR_BRIDGE_UINPUT_ENABLED=1 CURSOR_BRIDGE_WINDOW=<glass|ide>"
"$HOME/.venvs/universal/bin/python" scripts/cursor-bridge-launch.py open-tab \
  --force --thread "paste-$(date -u +%H%M%S)" --slug cursor-paste \
  --message-file <message-file>
```

4. Quote `ok`, `keystroke.steps`, `focused.title`, `focused.identifier`, and the host exported in step 3.

Paste chord is Ctrl+Shift+V in `scripts/orchestrator_tab_keystroke.py` and `scripts/cursor_tab_keystroke.py`. Do not send Ctrl+V alone.
