"""Tier-M authoring block for the operator-proxy mission briefing.

Kept out of ``operator_proxy_mission`` so the seat-map briefing stays readable
and this text can be revised without touching prompt-assembly logic. Content
authority: Fable CDP↔cursor-auto lane consult (2026-07-29) §2, §3.2, §5.
"""

from __future__ import annotations

# Shared-lib propagation consumers — harvest mints one row per slug (harvest-restart-propagation AC6).
# GIW reaches this module via operator_proxy_mission; mcp never imports it.
CONSUMERS: tuple[str, ...] = ("git_integration_worker",)

TIER_M_HEADING = "## Tier-M tool ask — DIRECTIVE template (BINDING)"
WIRE_NEUTRAL_HEADING = "## Wire-neutral authoring (BINDING)"
DEGRADE_LADDER_HEADING = (
    "## Degrade ladder — handler_status → prescribed move (BINDING)"
)

_TIER_M_TEMPLATE = """\
```
TYPE: DIRECTIVE
contract: implement
arc: <root-thread> / <slug>
assumed_state: <what this seat believes is true before the op>
intent: <the single tool call to fire, verbatim args>
scope: tool-op <tool>.<op> only; out-of-scope: repo writes, any second op
tool_op: <tool>.<op>
effects_expected: <the observable result, e.g. raw pull JSON relayed inline>
files_expected: none — tool-op directive
authority: cursor decides retry/backoff once; return on any auth prompt
AC: closeout body carries the raw result JSON verbatim (or the error verbatim)
evidence_required: raw JSON inline if ≤40 lines, else sidecar cortex:// URI + digest
density: sparse
budget: ≤2
vision: mechanical — tier-M surface asymmetry relay; no design content
```

`tool_op:` and `effects_expected:` are first-class scope tokens — a tool ask no
longer has to borrow `files_expected: none` to clear the scope gate. `vision:`
is still required for `implement` / `investigate`. A blocked reply carries
`missed_tokens` plus a `fix_hint` naming the exact lines to add: fix the named
lines and re-issue on the same thread. **One live request per private thread** —
a second `agent_bus.request` cancels a predecessor **only when that predecessor
is already `claimed`** (and the path is not a continuity hop). Queued predecessors
are **not** cancelled — both may run. The protection is weakest under backlog
(slow admit → re-issue against still-queued → both run): **wait**; a missing admit
is not a lost enqueue. `superseded: null` is not "lane clear"; a populated block
is an interrupt **attempt** (`run_cancel` = stop; `pre_register_live_run` =
displacement without process-stop). Parallel asks need separate lanes or one
bundled DIRECTIVE. See `cdp-operator-proxy` § Interrupt / supersede.

Optional on any DIRECTIVE: `deadline: +15m` (or an ISO-8601 stamp) — a job still
queued past it terminates `status:failed reason=expired` instead of running stale
intent against a moved world. Omit it and there is no TTL; Auto never invents
one. An unparseable value blocks rather than being ignored."""

_WIRE_NEUTRAL = """\
**Status: pending operator ratification** — wire-neutral body upgrade is
implemented server-side; Kaywan has not yet ratified the authoring pattern as
standing operator doctrine.

Wire `contract` is an admission/routing label, ¬ a permission claim: a tier-M or
implement ask MAY ship wire `contract=answer` (or omit it) while the body carries
`TYPE: DIRECTIVE` + `contract: implement`. The server upgrades the effective
contract from the body and **every** ULG-side gate still runs — admit scope,
`vision:`, relay-trust, `auth_gate_budget`, `require_attended`. Valid wire
contracts: `answer`, `confer`, `investigate`, `implement`, `verify`, `execute`,
`propagate`, `seed`.
`consult` is not a wire contract (aliases to `confer` with a deprecation note
today; unknown contracts are rejected 422 before the turn is written)."""

EXECUTE_HEADING = "## `contract: execute` — one bounded tier-M op (skeleton)"

_EXECUTE_CONTRACT = """\
**Status: operator ratified (2026-07-29)** — manifest rows match
``cortex://notes/system/specs/tier-m-tool-allowlist-manifest-v0.md`` §3 + §7.
The in-seat runner is live; code-surface ops
(``observability.query``, ``cortex.search``, ``cortex.entity_get``) fire when
the worker invoker is registered. ``email.*`` allow rows bind at the life-MCP
surface — admission may pass but execution refuses
``execute_invoker_unconfigured`` until a life-surface bridge is operator-ratified.

`execute` is for a single enumerated tool op with no repo writes. Auto fires it
**in seat** — one tool call does not justify a nested dispatch — and relays the
raw payload inline so a codeblind seat can disposition it without reading a file.

```
TYPE: DIRECTIVE
contract: execute
tool_op: email.pull
tool_args: {"mode": "folder", "folder": "INBOX", "limit": 3}
effects_expected: raw pull JSON relayed inline
```

Admission is the manifest, not a regex: exactly one `tool_op:`, allowlisted for
unattended firing, plus `effects_expected:` and (when the op takes arguments) a
single-line JSON `tool_args:`. Deny-by-default — an op with no row is refused.
Allowed today: read-only relays (`email.pull`, `email.search`,
`observability.query`, `cortex.search`, `cortex.entity_get`). Denied: anything
effectful (`email.send`, `email.move`, `email.delete`, `cortex.assert`) and all of
`fs.*`, `manage.*`, `pipeline.*`. A refusal names the reason, the allowed set, and
a `fix_hint`; a multi-op ask is refused as judgment — route it `implement`."""

PROPAGATE_HEADING = "## `contract: propagate` — operator restart request (BINDING)"

_PROPAGATE_CONTRACT = """\
**Status: live (2026-07-29)** — operators request drain-gated service restarts via
cursor-auto without tier-M ``manage.*``. Auto mints structured propagation ledger
rows and fires ``sync_restart`` through manage.sock when the safe-window matrix and
GIW I2 permit. Tier-M ``execute`` + ``manage.sync_restart`` remains denied.

Use when landed code must go live and the operator seat cannot (or should not) call
``manage`` from Cowork directly.

**Shorthand (one service):**

```
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart mcp
code_ref: <land SHA or omit for HEAD>
allow_self_preempt: true
effects_expected: propagation row persisted; restart executed or deferred with reason
density: sparse
budget: ≤1
```

``allow_self_preempt`` defaults **True**. When True, cursor-auto may auto-escalate
to ``force=true`` on self-heat busy deferrals for ``mcp``/``cdp_ask``. Set
``allow_self_preempt: false`` to veto that auto-escalation. Explicit ``force: false``
is **not** the auto-escalation veto.

**Structured (multi-service or explicit matrix):**

```
TYPE: DIRECTIVE
contract: propagate
scope: propagation sync_restart
effects_expected: propagation rows persisted; per-row execution status relayed inline

## propagation
```yaml
propagation:
  - service: mcp
    code_ref: <land SHA>
    proof_class: client_visible
    allow_self_preempt: true  # default; false vetoes auto-escalation (force: false does not)
    # omitted proof → composed from proof_class (not from service default)
  - service: git_integration_worker
    code_ref: <land SHA>
    safe_window: drain_required
    proof_class: process_live
    # omitted proof → process-identity obligation (compose_proof), never OpenAPI prose
    hazard: closeout_relay
```
```

Omitted ``proof`` is composed from ``proof_class`` at mint time. ``process_live``
yields process-identity prose; do not rely on the service's default class.

**Derivation tags (BINDING):** a propagation row ``reason`` carries
``derived:`` / ``import_path:`` tags **iff** a generator derived the row
(path-prefix service mint, CONSUMERS mint, or tagged RESIDUE coerce). Hand-authored
rows in this DIRECTIVE stay **untagged** — do not invent tags. Absence means
seat-authored; that silence is informative only while every derived row is tagged.

Closeout carries ``propagation[]``, ``row_ids``, and ``executions[]`` per service.
``disposition: executed`` only when proof-of-live observed; ``queued`` when manage
deferred **with a persisted restart intent** (``restart_intent_id`` present — manage
owns the queue and will fire after drain); ``blocked`` when manage deferred busy
with **no** persisted intent (nothing will fire automatically); ``submitted`` when
restart kicked but proof pending. Retired tokens: ``scheduled``, ``parked`` (lied as
in-flight / dead-end). Live only when ``code_version`` matches ``code_ref``.

**Nesting ban (closeout_relay):** do **not** fire ``contract:propagate`` from inside a
GIW write-lease-holding nested dispatch whose parent closeout must relay
dispositions. Thread 6692 (``failed/dead_on_giw_restart``) is the observed failure:
nesting propagate inside such a dispatch **loses the parent closeout when the
restart lands** — AutoJobQueue is process-local and a GIW drain/restart wipes
in-flight jobs (dead, not expired). The nested seat cannot observe the restart it
blocks. Fire propagate from a seat outside the GIW lease (parent cursor-auto after
nested exit, or operator-proxy top-level)."""

_DEGRADE_LADDER = """\
- `auto-admit-armed` — Auto is running it; poll the returned `poll_hint` in one
  continuous hold up to `wait_seconds ≤ 60` (life MCP client ceiling);
  re-arm only after an empty return or for nests that outlast one hold.
- `no-auto-handler` — the turn was written but nothing will act on it; the ask is
  parked. Re-`request` after liveness returns, or `send` + park. Never long-wait.
- `status:blocked (reason)` — authoring defect; fix per `missed_tokens` +
  `fix_hint` and re-issue on the same thread (same-thread re-issue supersedes a
  **claimed** in-flight job only — queued peers are not cancelled; see § Interrupt /
  supersede).
- `status:needs-attended (reason)` — surface the reason verbatim to the operator;
  Auto refuses to run it unattended.
- `status:done` + `disposition: declined` — nothing executed (answer contract has
  no in-seat execution path); follow the `routing_hint`, do not read it as work.
- `status:done` + `disposition: executed` — an `execute` op ran; the payload under
  `tool_payload` is the observed result. No payload ⇒ it did not run.
- `status:done` + `disposition: propagated` / `executed` / `queued` / `blocked` — a `propagate`
  restart request ran; read `executions[]` per service
  (queued/submitted/executed/blocked). ``queued`` = manage persisted a restart intent
  (will fire; poll liveness). ``blocked`` = manage busy deferral with no intent
  (nothing will fire). Retired: ``scheduled`` / ``parked``.
- `status:failed` / `status:superseded` — a negative status is a *claim*: observe
  independently before re-issuing anything effectful."""


def tier_m_authoring_block() -> str:
    """Return the tier-M template + wire-neutral + degrade-ladder briefing text."""
    return "\n\n".join(
        (
            TIER_M_HEADING,
            _TIER_M_TEMPLATE,
            EXECUTE_HEADING,
            _EXECUTE_CONTRACT,
            PROPAGATE_HEADING,
            _PROPAGATE_CONTRACT,
            WIRE_NEUTRAL_HEADING,
            _WIRE_NEUTRAL,
            DEGRADE_LADDER_HEADING,
            _DEGRADE_LADDER,
        )
    )


__all__ = [
    "DEGRADE_LADDER_HEADING",
    "EXECUTE_HEADING",
    "PROPAGATE_HEADING",
    "TIER_M_HEADING",
    "WIRE_NEUTRAL_HEADING",
    "tier_m_authoring_block",
]
