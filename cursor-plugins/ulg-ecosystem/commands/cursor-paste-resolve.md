Open a Cursor agent, via `/cursor-paste`, to resolve one friction or assertion.

## Invocation

```
/cursor-paste-resolve <friction|assertion> <id> [<glass|ide>] [<orion-node|jupiter>]
```

After kind and id, zero, one, or two target tokens. The sets do not overlap, so order does not matter. Classify each token by its set.

| Arg | Allowed | When omitted |
|---|---|---|
| kind | `friction` or `assertion` | required |
| id | positive integer | required |
| window | `glass` or `ide` | `glass` — Cursor Glass, not the IDE |
| host | `orion-node` or `jupiter` | the one host this request's context names |

A missing window focuses Cursor Glass. A missing host is never filled with `jupiter` or `orion-node` by default. The launch script's unset-host default is `jupiter`. Attended Glass living on `orion-node` is not a host for this command. Use a host only when this invocation names it, or this request's context names exactly one of the two.

## Refuse

- Kind missing or not in its allowed set. Stop and name the two kinds.
- Id missing or not a positive integer.
- A target token in neither set, or two tokens in the same set. Do not launch.
- Host omitted and this request's context does not name exactly one of `orion-node` or `jupiter`. Stop and name the two hosts. Do not launch.
- `assertion_get` does not return that id. Do not launch.

## Steps

1. Parse kind and id. Bind window to the window token, or `glass` when that token is absent. Bind host to the host token, or to the single host named in this request's context when that token is absent. Write the four values down before any file write. An unresolved host is a stop.
2. Confirm the id:

```
cortex(tool="assertion_get", arguments='{"assertion_id": <id>}')
```

Friction rows are assertions. A missing row is a stop.
3. Read `cortex://notes/system/prompts/work-item-implementer-friction.md` at this moment (`fs` `op=read`). Do not use a copy stored in this command.
4. Write `tmp/prompts/cursor-paste-<kind>-<id>.md`. The file is the full prompt `/cursor-paste` will send. First lines, then the prompt bytes unchanged:

```
Rename this chat tab to <kind>:<id>.

You are resolving <kind>:<id>.

```

5. Call `/cursor-paste` with both target tokens filled in. Follow `cursor-plugins/ulg-ecosystem/commands/cursor-paste.md` for that call. Do not restate its exports, launch script, or keystroke steps here.

```
/cursor-paste <window> <host> tmp/prompts/cursor-paste-<kind>-<id>.md
```

6. Report with the fields `/cursor-paste` requires, plus kind, id, window, and host. Say whether the host was explicit or taken from this request's context.
