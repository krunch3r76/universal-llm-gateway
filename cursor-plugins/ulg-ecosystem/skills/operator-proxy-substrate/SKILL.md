---
name: operator-proxy-substrate
description: "Cursor-side operator-proxy substrate — admit gates, nest_under/lease, auth-gate budget enforcement, supersede revert, GIW drain hazard, CDP packet + chip delivery. Companion to cdp-operator-proxy (shared contract)."
---

# Operator-Proxy Substrate — cursor side

`cursor_only` companion to **`cdp-operator-proxy`** (`shared_sync`).

**Carve rule (BINDING):** `shared_body ⇒ observable protocol behavior the operator seat
executes or must expect` · `code_body ⇒ mechanism producing that behavior`. Doctrine:
`decision:operator-proxy-skill-surface-split`.

| Concern | Owner |
|---|---|
| Shared operator contract — DIRECTIVE / CLOSEOUT / DISPOSITION, invariants 0–33 | `cdp-operator-proxy` |
| Protocol SOT — field tables, transport, handler wiring | `cortex://notes/system/specs/cdp-operator-proxy-v0.md` |
| Work-posting SOT — tick admit, B1/B2, mission launch | `cortex://notes/system/specs/cursor-auto-tick-work-posting.md` |
| CDP / Jupiter transport, harvest, converse, skill delivery | `claude-ai-cdp-navigation` (`cursor_only`) |

Neither skill is the protocol SOT; both are seat-facing digests.

## When

Authoring / admitting / debugging an operator-proxy DIRECTIVE from the cursor or `cursor-auto`
side · chaining a nested `cursor-sdk` hop under a live lease (`nest_under`) · diagnosing a
`status:blocked` admit (auth-gate budget, scope token, `vision:`) · standing up a CDP boot packet
that must deliver Claude skill chips · reasoning about supersede revert honesty, relay-trust
population, or GIW drain.

**Not** on the life / Cowork operator seat — `cursor_only` means this body is not attachable
there and none of these recipes are executable from that surface.

## Nesting + lease — mechanism behind `cdp-operator-proxy` inv 19

The whole ladder runs as **`cursor-sdk` nested dispatches**, because CDP Opus dispatches *through*
`cursor-auto`. Chained dispatches MUST carry `nest_under` = the live lease holder's
`dispatch_id`; a hop treated as fresh top-level **contends** with the shared-checkout lease
instead of parking under it.

| Element | Where |
|---|---|
| Live holder id | `CursorDispatchLedger.lease_snapshot().holder_dispatch_id` |
| Resolution | `handler._resolve_nest_under` on `nest_park` |
| Depth cap | LIFO park stack, hard cap **depth 10** → 422 `CURSOR_NEST_DEPTH_EXCEEDED` |

**Tick-background host:** when **manage** admits a background `cursor-sdk` poll window so the
IDE / Cowork session may close, that host holds the lease — Opus→cursor-auto implement dispatches
during that window are nested under the tick holder. Cowork-attended `request` *without* a tick
background host is the case where `cursor-auto` is top-level.

Composes: `lean-context-dispatch-first` · `anthropic-dispatch-authorization` ·
`cdp-operator-proxy` inv 13.

## Auth-gate budget — admit-time enforcement (BINDING)

Enforced **at admit**, before `status:admitted` / nested SDK. Classified auth-gate CLOSEOUTs on
the same private request thread count toward a failure budget — not dispatches, not turns.

| Phase | Budget | Block trigger |
|---|---|---|
| **Pre-ack** (no valid `auth_gate_ack` yet) | **2** classified failures | Third `implement` DIRECTIVE → `status:blocked` + `reason: auth_gate_budget_exhausted` |
| **Post-ack** (valid ack precedes the counting window) | **1** classified failure | Second post-ack `implement` after that failure → blocked |

Waiters must complete on `status:blocked` (`poll_hint.alternate_completions`); **no** nested
Composer burn on block.

**Known bypass (real):** a new thread / `new_slug` for the same task resets the counter. The v1
gate caps substrate spend on **one lane**; it does not cap operator determination.

Operator-facing signals + `auth_gate_ack:` grammar: `cdp-operator-proxy` § Auth-gate budget.

## Supersede mechanism (BINDING)

A second `agent_bus.request` on the **same private thread** while a job is in flight is read as a
**backtrack**, not a queue append — no extra tool, no body token, no `manage`, no GIW restart.

| # | Step | Evidence |
|---|---|---|
| 1 | Cancel the live nested cursor-sdk run (bridge `CancelRun`; hard bridge abort if refused) | log `cursor-sdk supersede signalled … method=run_cancel\|bridge_abort` |
| 2 | Close the dead job as **`status:superseded`** | terminal turn `status:superseded — <subject>` |
| 3 | Revert the void episode's **git-tracked** writes from its admit baseline | log `supersede revert … restored=N`; counts in the new episode's preamble |
| 4 | Open the new DIRECTIVE with a `SUPERSEDE NOTICE` naming the void dispatch and residue | first block of the new agent's prompt |

**Revert fails closed.** Step 3 restores git-tracked paths only; a missing admit baseline returns
`ok=false` rather than implying a clean tree. Created (untracked) paths are reported, never
deleted — a shared checkout cannot safely remove unattributed paths
(`shared-checkout-housekeeping_ulg.mdc`).

## Synthesized closeout relay-trust gate — wiring

**Status: disabled** — `RELAY_TRUST_SYNTHESIZED_GATE_ENABLED = False`. Substring population
admitted clean `section2_sidecar` closeouts when §2 prose merely *named* `section2_synthesized`;
the **meta-sourced** population fix has landed, re-enable is operator-gated after a restart probe.

When enabled, a nested SDK closeout carrying `closeout_source: section2_synthesized` blocks the
next DIRECTIVE until the operator posts `synthesized_closeout_ack:` on the same thread;
`verdict: ratify` does not clear it. `relay_trust_unverifiable` = bus history unreadable, distinct
from a real pending ack. **Mislabel class:** `section2_synthesized` + `unauthored` does not
reliably mean the executor failed to author §2 — the relay may have mis-picked an authored
sidecar, which is why the operator contract says *read `artifact_paths` / the cortex sidecar
before acking.* Ack grammar + deadlock remediation: `cdp-operator-proxy` § Synthesized closeout
ack.

## GIW drain vs CLOSEOUT relay (mechanism)

`git_integration_worker` hosts **cursor-auto**, the process-local **AutoJobQueue**, and the
**poll→relay loop**. Any drain / restart of that process risks losing the CLOSEOUT relay
**regardless of which service name was passed to `manage`** — drain defers the restart to
dispatch exit and wins over `post_operator_closeout`. Charter tick windows share the gate, so
tick-initiated GIW restarts can eat unrelated operator dispatches' relays. Hence the operator
contract: never place a GIW restart AC inside a DIRECTIVE whose §2 CLOSEOUT is awaited — use a
`contract: propagate` restart-only DIRECTIVE, or defer to RESIDUE and fire propagate separately.

The relay-trust gate itself is **bus-only** — it blocks cursor-auto admission, not restarts.
`contract: propagate` mints propagation ledger rows and coordinates drain-gated `sync_restart`
via manage.sock; tier-M `execute` + `manage.*` stays denied at the manifest.

## Packet skill delivery (BINDING — CDP)

Open the prompt with **`/<slug>` alone on its own line** (newline between slugs) before body
prose. Prefer `team_dispatch(model=cdp/…, skills=[…])` (server prepend), or the same slash header
in `project_ask` bodies. Prose `Use the … skill` alone is **not** chip delivery. SOT:
`claude-ai-cdp-navigation` § Skill delivery.

**Surface check before writing a chip line.** Only `shared_sync` / `life_local` slugs
(`config/skills.yaml`) exist on Customize Skills; a `/<slug>` header naming a `cursor_only` body
is **silently skipped**, and the inline body in the prompt is the fail-closed delivery path.
`operator-proxy-substrate` is itself `cursor_only` — never chip it to a CDP boot.

## cursor-auto ↔ tick mechanics

| Path | Mechanism |
|---|---|
| Life→code **direct** (B1) | cursor-auto executes / nests a specialist under its own lease (`nest_under` when the gate is held) |
| Life→code **tick handoff** (B2) | Auto mints/stamps → **releases** the lease → tick admits the worker; if Auto still holds the gate the tick worker MUST `nest_under=holder_dispatch_id` — silence ⇒ **25956 stall** |
| Auto holds `cursor_sdk_gate` | Further SDK work uses `nest_under` = holder; ¬ fresh top-level contend |
| Kernel implement while tick held / no root | cursor-auto nested implement **off-tick**; birth/enroll before claiming tick progress |
| Enrolled progress | Mint/stamp friction or todo with `charter_root` on an **enrolled** root → tick reconcile → `enroll_rows` → kernel admit |

**Forbidden:** Auto improvising tip enqueue; `enroll_rows` onto throwaway
`ensure_conveyor_root` roots that state_close; re-enrolling closed conveyor roots to park work;
B2 admit without the nest/release handoff.

## Admit-gate enforcement — scope, vision, fix hints

The tokens a DIRECTIVE must carry are the operator's authoring contract
(`cdp-operator-proxy` § Wire contracts + tier-M tool ask). This section owns how the gate
**matches** them and what it emits when they are absent.

| Gate | Matching | Block emission |
|---|---|---|
| Actionable scope | `has_actionable_scope` accepts a repo `scope:` field, a tier-M `tool_op:` + `effects_expected:` pair, or `scope: propagation …` / a `## propagation` heading. `files_expected: none` alone is **not** clearance | `missed_tokens` + `fix_hint` naming the exact lines to add |
| Vision | `vision:` required on `implement` / `investigate` | `vision_field_missing` |
| Mission close wake path | `TYPE: MISSION_CLOSEOUT` / subject `MISSION CLOSEOUT` must carry `## Work beyond this close` with wake tokens (`collector:` / `followup:` / `charter_enrolled:` / `operator_gate:`), or `none` when empty; `commissioned, in flight` alone refuses | `mission_close_wake_path_missing` / `mission_close_uncollected_commission` + `MISSION_CLOSE_WAKE_FIX_HINT` (`libs/claude_bundles/mission_close_wake.py`) |

A payload naming only what was missing is a dead end for a seat that cannot read the gate's code,
so every hint names the exact lines to add and the authoring seat re-issues without a round trip.
Do not weaken a hint to a bare token name.

**Enforcement coupling** — gate and doc must not drift; both cite live code:

| Doc claim | Code |
|---|---|
| §2 inline `scope:` is a first-class scope token | `services/git_integration_worker/cursor_auto/directive.py` — `_SCOPE_FIELD_RE` ("must match has_actionable_scope") |
| Blocked payloads point at the tier-M DIRECTIVE template | `services/git_integration_worker/cursor_auto/fix_hints.py` — `TIER_M_TEMPLATE_REF = "cdp-operator-proxy §2 (tier-M DIRECTIVE template)"` |

`TIER_M_TEMPLATE_REF` names the **shared** slug by design: the hint is read by the operator seat,
which can only load `cdp-operator-proxy`. Do not repoint it here.

## Operator-doctrine carve-out — cursor side (BINDING)

When the **subject** is `agent_skill:cdp-operator-proxy`, `agent_skill:operator-proxy-substrate`,
`cortex://notes/system/specs/cdp-operator-proxy-v0.md`, or
`decision:operator-proxy-seat-posture`, cursor MUST NOT (a) author a sealed prompt on it,
(b) mint a child ask-thread to put it to CDP Opus, or (c) open or drive the operator's `request`
lane.

The legal move is **`TYPE: OPERATOR_GATE`** — one line naming the open question plus corpus URIs —
to the operator's private request lane when known, else the standing root. Parking an
operator-doctrine question that way is **compliant**, not a stall. Execution is unchanged:
cursor-auto still executes every resulting write behind the shared-checkout lease. Full statement:
`cdp-operator-proxy` inv 13.
