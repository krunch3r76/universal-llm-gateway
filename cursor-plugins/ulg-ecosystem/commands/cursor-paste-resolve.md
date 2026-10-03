Open a Cursor agent, via `/cursor-paste`, to resolve one friction or assertion.

## Invocation

```
/cursor-paste-resolve <friction|assertion> <id> [<glass|ide>] [<orion-node|jupiter>] [maestro]
```

After kind and id, zero to three target tokens. The sets do not overlap, so order does not matter. Classify each token by its set.

| Arg | Allowed | When omitted |
|---|---|---|
| kind | `friction` or `assertion` | required |
| id | positive integer | required |
| window | `glass` or `ide` | `glass` — Cursor Glass, not the IDE |
| host | `orion-node` or `jupiter` | the one host this request's context names |
| notify | `maestro` | no maestro memo — closed title suffix is `complete` |

A missing window focuses Cursor Glass. A missing host is never filled with `jupiter` or `orion-node` by default. The launch script's unset-host default is `jupiter`. Attended Glass living on `orion-node` is not a host for this command. Use a host only when this invocation names it, or this request's context names exactly one of the two.

`maestro` is a notify token. It does not choose window or host. When present, the prompt preamble (before the inline general prompt) tells the resolving agent to memo maestro on agent-bus thread `12286` after the item is closed, and to use the closed title suffix `complete-to-maestro`. Thread `12286` receives that closure memo only. The preamble forbids passing it as `dispatch_thread_id`, posting the code review on it, or harvesting the review there. The preamble always tells the resolving agent to suffix the tab title with the current stage (`initial`, `review`, then the closed suffix).

## Refuse

- Kind missing or not in its allowed set. Stop and name the two kinds.
- Id missing or not a positive integer.
- A target token in neither set, or two tokens in the same set. Do not launch.
- Host omitted and this request's context does not name exactly one of `orion-node` or `jupiter`. Stop and name the two hosts. Do not launch.
- `assertion_get` does not return that id. Do not launch.

## Steps

1. Parse kind and id. Bind window to the window token, or `glass` when that token is absent. Bind host to the host token, or to the single host named in this request's context when that token is absent. Bind notify to `maestro` when that token is present, else absent. Write the five values down before any file write. An unresolved host is a stop.
2. Confirm the id:

```
cortex(tool="assertion_get", arguments='{"assertion_id": <id>}')
```

Friction rows are assertions. A missing row is a stop.
3. Read `cortex://notes/system/prompts/work-item-implementer-friction.md` at this moment (`fs` `op=read`). Do not use a copy stored in this command.
4. Write `tmp/prompts/cursor-paste-<kind>-<id>.md`. The file is the full prompt `/cursor-paste` will send. Bind the closed suffix: `complete-to-maestro` when notify is `maestro`, else `complete`. First the rename + stage block, then — only when notify is `maestro` — both paragraphs in the maestro block below, then the prompt bytes unchanged:

```
Rename this chat tab to <kind>:<id> · initial.

You are resolving <kind>:<id>.

Keep the title base <kind>:<id>. Append exactly one stage suffix and replace it when the stage changes (do not stack suffixes):

| Suffix | When |
|---|---|
| initial | first rename; until review or close |
| review | while verifying the fix |
| <closed-suffix> | after the item is closed |

```

When notify is `maestro`, insert this paragraph after that block and before the general prompt:

```
When this <kind> is closed, inform maestro: send a memo on agent-bus thread 12286 reporting the closure (kind, id, and outcome). Do this after closure, before you stop.

Thread 12286 is that closure memo only. Do not pass 12286 as dispatch_thread_id, do not post the code review on it, and do not harvest the review there. Mint the review thread the way the work-item prompt says. Glass and the IDE both arm a terminal for the harvest watcher.

```

5. Call `/cursor-paste` with both target tokens filled in. Follow `cursor-plugins/ulg-ecosystem/commands/cursor-paste.md` for that call. Do not restate its exports, launch script, or keystroke steps here.

```
/cursor-paste <window> <host> tmp/prompts/cursor-paste-<kind>-<id>.md
```

6. Report with the fields `/cursor-paste` requires, plus kind, id, window, host, and whether maestro was on the invocation. Say whether the host was explicit or taken from this request's context.
