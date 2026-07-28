"""Build the operator-proxy host six-block packet for one charter window.

When attendance resolves to ``operator_proxy``, the tick admits an unattended
cursor-sdk host (``operator_proxy_host_generate_body`` → generate ``read_only``)
that polls the CDP operator-proxy private lane, harvests CDP executions, and
re-admits to keep the operator seat live without an attended IDE or Cowork wait.
"""

from __future__ import annotations

from .checkpoint_parse import ParsedCheckpoint
from .checkpoint_schema import (
    append_footer_to_packet,
    footer_kwargs_for_window,
    output_format_footer_requirement,
)
from .residue_fingerprint import normalize_next_pickup


def _scope(window_index: int, root_id: str) -> str:
    return f"""\
<scope>
Goal: Charter-runner OPERATOR-PROXY host window {window_index} on agent-bus:{root_id}.
Attendance axis = operator_proxy: poll the CDP operator-proxy private lane (lane
thread id from CHECKPOINT Sidecars / corpus — not hardcoded), harvest CDP
executions, relay CLOSEOUTs, and post TYPE: TICK_STATUS digests on the tick root
so the charter-tick kernel rewrite advances without attended IDE or Cowork wait.
Default executor: cursor-sdk Grok (unattended generate wire, read_only host).
</scope>"""


def _invariants(root_id: str) -> str:
    return f"""\
<invariants>
[read-only-host] ``read_only=True`` on this dispatch — the host polls, harvests,
and writes bus/cortex provenance only. It never edits the checkout. Implement work
is fired by CDP Opus through cursor-auto as a separate nested dispatch.
[nesting] While this host holds the lease, any downstream implement dispatch MUST
pass ``nest_under`` = this holder's ``dispatch_id`` (LIFO park stack, hard cap
depth 10). A chained hop fired as a fresh top-level dispatch contends with the
shared-checkout lease instead of parking under it.
[transport-only] This host MUST NOT author DIRECTIVEs, take the operator seat, or
answer on Opus's behalf. Transport and liveness only — per
``cortex://notes/system/threads/6036-continuous-operator-proxy-drive.md``.
[mark-read] Read the latest turns on the operator-proxy private lane before any
further ``request``. ``mark_read`` through the latest turn first — unread addressed
turns cause HTTP 409 ``unread_turns_exist`` (cdp-operator-proxy §7b).
[poll-ladder] If a DIRECTIVE is open and cursor-auto is in flight, poll via
``agent_bus.wait`` from the pinned ``poll_hint``. Long-running is NOT stalled. If
a CDP execution is in flight, poll ``project_ask(op="poll", ...)`` until
``archive_uri``, verified ``content_proof``, or ``failed`` + ``stall_stage``. Once
``turn_idle`` or ``stop``, re-poll every 5–10s — never multi-minute sleeps.
[stall-park] If ``stall_stage`` is set, park rather than thrash — follow
``consult_stall_exhaust`` behaviour as reference.
[tick-status] On terminal harvest, relay CLOSEOUT onto the operator lane if not
already there, then post ``TYPE: TICK_STATUS`` digest on agent-bus:{root_id} — NOT
an operator-authored CHECKPOINT (CHECKPOINT authorship is cursor-owned).
[re-admit] If the arc is incomplete, Next-pickup MUST preserve enough state for
the next tick to re-admit another host window so the operator seat stays live.
- Use the `cdp-operator-proxy` skill (canonical slug — seat self-fetches)
- Use the `claude-ai-cdp-navigation` skill § Dual-completion poll ladder
- Use the `agent-bus-discipline` skill (canonical slug — seat self-fetches)
</invariants>"""


def _window_identity_block(parsed: ParsedCheckpoint) -> str:
    pickup_rows = normalize_next_pickup(parsed)
    pickup_line = pickup_rows[0] if pickup_rows else "(none)"
    ref = parsed.source_ref or "(unresolved)"
    return (
        "## Window identity (BINDING)\n"
        f"- normalized_next_pickup: {pickup_line}\n"
        f"- source_ref: {ref}\n"
    )


def _task_guidance(
    *, root_id: str, scoreboard_line: str, identity_block: str
) -> str:
    return f"""\
<task_guidance>
## Resume step 0 (do first)
1. Load cdp-operator-proxy + claude-ai-cdp-navigation (§ Dual-completion poll
   ladder) + agent-bus-discipline (§ Standing root threads).
2. {scoreboard_line}read the latest CHECKPOINT on agent-bus:{root_id} — resolve
   the operator-proxy private lane thread id from Sidecars / corpus (not hardcoded).

{identity_block}
## Operator-proxy host work (this seat owns poll→harvest→re-admit)
### Lane read (mandatory before request)
- Read latest turns on the operator-proxy private lane.
- ``mark_read`` through the latest turn before any further ``request``.

### Poll ladder
- DIRECTIVE open + cursor-auto in flight → ``agent_bus.wait`` from pinned
  ``poll_hint``. Long running ≠ stalled.
- CDP execution in flight → ``project_ask(op="poll", ...)`` until terminal
  (``archive_uri``, ``content_proof``, or ``failed`` + ``stall_stage``).
- On ``turn_idle`` or ``stop``, re-poll every 5–10s — no multi-minute sleeps.

### Terminal harvest
- Relay CLOSEOUT to the operator lane if not already present.
- Post ``TYPE: TICK_STATUS`` digest on agent-bus:{root_id} — not a CHECKPOINT.
- If arc incomplete: Next-pickup preserves re-admit state for the next tick.
- If ``stall_stage`` set: park per consult_stall_exhaust; do not thrash.

## Acceptance criteria
1. Operator lane polled; unread turns cleared before further requests.
2. In-flight dispatches polled to terminal or parked on stall.
3. TICK_STATUS digest posted on harvest; arc state preserved for re-admit when needed.

## Stop conditions
TICK_STATUS boundary · stall_stage park · unresolvable transport ⇒ park and preserve
Next-pickup for re-admit.
</task_guidance>"""


def _corpus(root_id: str, scoreboard_uri: str | None) -> str:
    return f"""\
<corpus>
Charter root agent-bus:{root_id}. Scoreboard: {scoreboard_uri or '(see latest CHECKPOINT)'}.
Latest CHECKPOINT on the root is the only state source.
Design: cortex://notes/system/threads/6036-continuous-operator-proxy-drive.md.
Protocol: cortex://notes/system/specs/cdp-operator-proxy-v0.md · skill cdp-operator-proxy.
Operator-proxy lane thread id: resolve from CHECKPOINT Sidecars / corpus — not hardcoded.
</corpus>"""


_MCP_CAPABILITIES = """\
<mcp_capabilities>
LIFE/CORTEX MCP: ON — cortex, agent_bus, fs (cortex sandbox).
CODE/VORTEX MCP: ON — workspaces fs (read-only; checkout writes unavailable),
team_dispatch, agent_bus wait/poll_hint, project_ask.
This seat polls the operator-proxy lane and CDP executions; it does not edit the
checkout (read_only host). Nested implement dispatches fire from CDP Opus via
cursor-auto under ``nest_under`` = this holder's dispatch_id.
</mcp_capabilities>"""


def _output_format(root_id: str, window_index: int) -> str:
    window_id = f"charter-{root_id}-w{window_index}"
    footer_req = output_format_footer_requirement(window_id=window_id)
    return f"""\
<output_format>
Post ``TYPE: TICK_STATUS`` digest on agent-bus:{root_id} — NOT an operator-authored
CHECKPOINT. On incomplete arc preserve Next-pickup state for the next tick to
re-admit another operator-proxy host window. On stall, park and preserve poll_hint /
from anchors for re-admit.
{footer_req}
</output_format>"""


def materialize_operator_proxy_packet(
    root_id: str,
    parsed: ParsedCheckpoint,
    *,
    scoreboard_uri: str | None = None,
    window_index: int = 1,
) -> str:
    """Return an operator-proxy host six-block packet."""
    scoreboard_line = (
        f"read the scoreboard at {scoreboard_uri}, then "
        if scoreboard_uri
        else ""
    )
    identity_block = _window_identity_block(parsed)
    body = f"""\
{_scope(window_index, root_id)}
{_invariants(root_id)}
{_task_guidance(
    root_id=root_id,
    scoreboard_line=scoreboard_line,
    identity_block=identity_block,
)}
{_corpus(root_id, scoreboard_uri)}
{_MCP_CAPABILITIES}
{_output_format(root_id, window_index)}
"""
    return append_footer_to_packet(
        body, **footer_kwargs_for_window(root_id, window_index)
    )


def operator_proxy_subject(root_id: str, window_index: int) -> str:
    """Bus subject for an operator-proxy host admission."""
    return (
        f"Charter-runner operator-proxy host window {window_index} — "
        f"agent-bus:{root_id} (polls CDP lane)"
    )


__all__ = ["materialize_operator_proxy_packet", "operator_proxy_subject"]
