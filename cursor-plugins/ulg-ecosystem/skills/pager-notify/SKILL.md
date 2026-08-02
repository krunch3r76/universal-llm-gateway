---
description: "On operator wake/ping/SMS/pager — email-bridge Fi notify via UDS curl or pager_notify lib; no personal endpoints in prose."
---

# Operator Pager (email-bridge)

## When to read

Read when the operator asks to be woken, pinged, SMSed, or paged — or when a long wait (queue drain, rebuild, deploy, hold clear) should notify them outside chat without babysitting the pane.

**Not** for mailbox IMAP/`dispatch(tool="email")` workflows — those are `email-tool-dispatch` + `email-bridge-mailbox`. This skill is **outbound operator notify only**.

## Surface class (`cursor_only`)

This skill is **`surface_class: cursor_only`** — recipes here run on **Cursor / code seats** with host reachability to email-bridge UDS. The **Cowork operator seat (life MCP) cannot execute any recipe in this skill**; reading it as permission to page from the operator seat is a misread. Life-seat attention delivery is **`notify` on life MCP** (server-side proxy to `/pager/notify`). While `notify` is absent, route via `agent_bus.request` to cursor — do not park on the operator in prose.

## Invariant

`operator_wake ⇒ POST email-bridge /pager/notify` · `¬ embed personal addresses/phone/numbers in skills, rules, packets, or chat` · delivery identity stays in email-bridge/Fi config, not agent prose.

email-bridge is a **ULG host satellite** (UDS under `/tmp/universal-protocol/`). Treat it as fleet infra, not a third-party side channel.

## Endpoint (curl)

```bash
SOCK="${EMAIL_BRIDGE_SOCK:-/tmp/universal-protocol/email-bridge.sock}"
curl -sS --unix-socket "$SOCK" \
  -H 'Content-Type: application/json' \
  -d '{"subject":"<≤120>","body":"<≤4000>","tag":"<≤40>"}' \
  http://localhost/pager/notify
```

Success: JSON with `"status":"sent"`. Missing sock / `503` ⇒ email-bridge down or SMTP relay unwired — check `manage` health for `email_bridge`, ¬ invent alternate personal endpoints.

Health probe:

```bash
curl -sS --unix-socket "$SOCK" http://localhost/health
```

## Library path (Python)

`libs/pager_notify` — `notify_pager(subject, body, tag="")` via `DEFAULT_EMAIL_BRIDGE_URL` (`unix://…/email-bridge.sock`). Prefer this inside fleet Python; prefer **curl UDS** for shell/tmux watch scripts the operator can see.

Env kill-switch: `PAGER_NOTIFY_ENABLED=0` disables the lib client.

## Payload discipline

| Field | Cap | Guidance |
|---|---|---|
| `subject` | 120 | Short so-what (`ULG GIW clear`, `ULG hold ready`) |
| `body` | 4000 | Full awareness NL — **vision/architecture-shaped**, not a status dump; no secrets, no PII |
| `tag` | 40 | Machine label for logs (`giw-clear`, `admit-gate-live`) |

### Body shape (BINDING — operator 2026-08-02)

Generality is welcome; **hollowness is not**. Every awareness page (progress *and* mission-debrief) must be readable as: what this means for how the fleet works, what just changed, what happens next — not a slug/status telegram.

| Slot | Required? | What it is |
|---|---|---|
| **Vision** | **Always** (open the body) | What this is *for* in how the fleet knows / acts — the gap it closes; ¬ what was built |
| **Looking back** | Mission-debrief **always**; progress **when a premise moved** or a false finish almost landed | What we thought vs what held; the correction |
| **Architecture** | **Always** (one sentence) | The load-bearing distinction a non-engineer can hold |
| **Looking ahead** | **Always** | The next concrete move that advances the *mission*, not “check the logs” |
| **Beyond this close** | Mission-debrief **always** (wake tokens); progress when residuals exist | Who collects what next |

Host composers: `libs/pager_notify/mission_page.py` (`format_mission_awareness_page` / `format_summons_stop_page`). Mission-debrief prose SOT: `cdp-operator-proxy` § Mission-debrief format (vision → accomplishments by importance → reframe → architecture → …).

**Anti-patterns:** “loop stopped — reason=X · lane=N” · subject-only so-what with empty body · forward-looking that is only “read the sidecar” · debrief with no vision sentence.

## When to page (operator-facing)

Three classes — **do not conflate**:

| Class | Subject shape | operator action |
|---|---|---|
| **Awareness — progress** | NL so-what **without** `COME TO IDE` | Optional read; **¬** open Cursor |
| **Awareness — mission debrief** | One architecture-first paragraph (named ULG systems + vision) **without** `COME TO IDE` (full body on pager) **plus** required `Beyond this close: …` line (in-flight / scheduled / enrolled / awaiting harvest — who collects and how; or `none`) · tag `mission-debrief` | Optional read; **¬** open Cursor; durable sidecar for later fetch |
| **Interrupt** | **`COME TO IDE`** / `NEED IDE` | Open Cursor — **problem only** (options exhausted / true operator-only IDE gate) |

| Situation | Page? | Class |
|---|---|---|
| Progress / insight / interesting note seat judges he'd like | **Yes (welcome)** — NL body; record-first | Awareness — progress |
| Operator said “ping me when X” / “wake me” | **Yes** — on X true | Usually awareness; interrupt only if X was “come to IDE when…” |
| Mission/episode close — debrief | **Yes** — **full** one-paragraph body + `Beyond this close: …`; subject **¬** `COME TO IDE`; tag `mission-debrief` (life `notify` refuses without the beyond line) | Awareness — mission debrief |
| All other options exhausted (fleet cannot clear) / problem needs IDE | **Yes** — subject **`COME TO IDE`** | Interrupt |
| Ordinary CLOSEOUT / admitted / blocked-resolving | Awareness OK; **¬** `COME TO IDE` | Awareness — progress |
| Multi-minute wait they left (queue, rebuild) | **Yes** on terminal — interrupt only if they must act in IDE | Per predicate |
| Every status poll / debug mailbox | **No** | — |

Doctrine: `decision:operator-proxy-seat-posture` a:27360 · `agent_skill:cdp-operator-proxy` inv 22(d).

## Watch-script pattern (tmux-visible)

Operator can watch a pane; script polls a predicate then pages once:

1. Loop: probe condition (GIW dispatch-status, hold file, health).
2. Require **stable clear** (2 consecutive positives) before notify.
3. One `POST /pager/notify`, print response, exit.
4. Log path under `/tmp/…` so the pane is the SOT while running.

Example already used this session: `/tmp/watch-giw-clear-and-page.sh` (predicate = write-lease queue empty).

## Anti-patterns

| Bad | Good |
|---|---|
| Put operator phone/email in a skill or packet | Call `/pager/notify`; bridge owns delivery |
| “Email the operator” via raw SMTP from agent | UDS pager endpoint only |
| Silent background Cursor shell for “ping me” | tmux-visible script + pager on done |
| Page on every tick refuse / log line | Page on operator-requested milestones |
| Conflate with Outlook/Graph or IMAP search | Separate surfaces; this skill = Fi notify |
| Status/reason telegram with no vision or next | Vision → (look-back) → architecture → look-ahead → Beyond |
| Mission-debrief that never says what changed about knowing | Open on the gap the system used to leave |

## Composes with

- `email-tool-dispatch` / `email-bridge-mailbox` — mailbox ops, not pager
- `service-lifecycle` — `email_bridge` start/health via `manage`
- `operator-posture` — wake only when they asked or a real away-from-chat gate
