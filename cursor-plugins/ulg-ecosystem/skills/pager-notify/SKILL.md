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

### Body shape (BINDING — operator 2026-08-02 · growth-map 2026-08-04 · so-what 2026-08-04)

Generality is welcome; **hollowness is not**. Every awareness page (progress *and*
mission-debrief) must be readable as a **growth map**: what this means for how the
fleet works, **which ULG systems grew or were added**, what just changed, what
happens next — not a slug/status telegram.

**So-what bar (BINDING — the page must answer all three):**

1. **Future of ULG** — what does this mean for ULG’s capabilities going forward?
2. **Delta since before** — what is improved relative to the prior state (the gap closed)?
3. **Consumers of the repo** — how does this affect people *and agents* that consume
   this checkout (APIs, skills, bus protocol, MCP surface, invariants they must honor)?

| Slot | Required? | What it is |
|---|---|---|
| **Vision** | **Always** (open the body) | Grounded in **ULG vision statement(s)** (`cortex-vision.md` / cognitive-platform / architectural-vision) — what this is *for* in how the fleet knows / acts; **¬** the current mission’s local narrative; ¬ what was built |
| **Looking back** | Mission-debrief **always**; progress **when a premise moved** or a false finish almost landed | What we thought vs what held; the correction (**delta since before**) |
| **Architecture** | **Always** (one sentence) | The load-bearing distinction a non-engineer can hold — **must name concrete ULG systems** (`CSE Session Registry`, `project_ask`, `cdp-registry`, `agent-bus`, `cortex`, `git_integration_worker`, …) |
| **Looking ahead** | **Always** | Next concrete capability / consumer-facing consequence for ULG — not “check the logs”; mission choreography alone is insufficient |
| **Beyond this close** | Mission-debrief **always** (wake tokens); progress when residuals exist | Who collects what next |

**Growth-map phone test:** after reading the page, can you answer the three so-what
questions above **and** name systems ULG gained? If the answer is only a
ticket/slug/DISPOSITION or a mission recap, rewrite.

Host composers: `libs/pager_notify/mission_page.py` (`format_mission_awareness_page` /
`format_summons_stop_page` / `extract_awareness_slots`). Auto MISSION_CLOSEOUT
debrief refuses hollow bodies (`mission_debrief_architecture_missing` /
`mission_debrief_systems_unnamed`). Mission-debrief prose SOT:
`cdp-operator-proxy` § Mission-debrief format (vision → accomplishments by
importance → reframe → architecture → …).

**Anti-patterns:** “loop stopped — reason=X · lane=N” · `Mission debrief — bus:N`
with stripped AC inventory · subject-only so-what with empty body · forward-looking
that is only “read the sidecar” · debrief with no vision sentence · architecture
sentence with no named ULG system.

### CDP mission runners (BINDING — operator 2026-08-03)

Life-seat `cdp/opus-5` / `cdp/fable` operator-proxy missions bind the same register via
`agent_skill:cdp-operator-proxy` inv 22(d)–(g). This skill is `cursor_only` (UDS recipes);
the **audience rule is shared**:

| Rule | Binding |
|---|---|
| Audience | Human principal — even when a model seat holds operator |
| Phone test | Readable without the bus open; so-what lead |
| Cadence | Progress pages need a human-facing premise move; ¬ conveyor-only DISPOSITION bursts |
| Forbidden lead | Item ordinals · `DISPOSITION`/`CLOSEOUT` tokens · `auto-*` · contract names · turn numbers |

SOT for mission-runner prose + debrief length: `cdp-operator-proxy` inv 22 + § Mission-debrief format.

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
| Ordinary CLOSEOUT / admitted / blocked-resolving with a plain fleet so-what | Awareness OK; **¬** `COME TO IDE` | Awareness — progress |
| Conveyor-only DISPOSITION (ordinal bump / wave admitted / ratify-without-reframe) | **No** — batch into the next human-facing page | — |
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
| Mission-debrief that never says what changed about knowing | Open on the ULG vision gap closed + consumer effect |
| CDP progress page that reads as the mission talking to itself | Phone-test: future capability · delta · repo consumers (agents incl.); ids in `ref` |
| Vision slot that narrates the mission episode | Ground in ULG vision statements; mission facts stay in look-back / architecture |

## Composes with

- `email-tool-dispatch` / `email-bridge-mailbox` — mailbox ops, not pager
- `service-lifecycle` — `email_bridge` start/health via `manage`
- `operator-posture` — wake only when they asked or a real away-from-chat gate
