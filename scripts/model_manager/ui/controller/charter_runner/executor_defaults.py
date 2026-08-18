"""Charter-runner executor binds — judgment (default) and implement.

Operator bind 2026-07-20: default agent = **Grok 4.6** on the coding
substrate. Wire: ``seat=cursor-sdk``, ``model=cursor/grok-4.6``.

Operator bind 2026-08-18: judgment windows stay on the Cursor Models pool
(``cursor/grok-4.6`` @ ``effort=xhigh``, ``fast=false``). The 2026-08-16
Sonnet 5 ``xhigh``/``1m`` pin (agent-bus:7405) drew Ultra Other Models
and exhausted the second pool in ~48h; Sonnet/Opus/Terra are explicit
pins only. Consult/CDP host shells stay on Composer (I/O-only — no effort
knob).

Composer still pins ``fast=true`` explicitly so window_log / admit notes record
the bind.

Grok exposes ``effort`` + ``fast`` only (no ``thinking`` knob — live
ListModels / ``cursor_capabilities``).

The implement bind pins ``cursor/composer-2.5``, which is already the seat
default (``config/agents.yaml`` ``cursor/sdk.default_model``, bound there
specifically to ``contract=implement``) — this pins the existing default rather
than introducing a new model. Passing it explicitly is still right so the
executor note and ``window_log`` record a concrete bind.

Step overrides (not this module): CDP Opus for Opus-class code review;
attended Composer handoff when eyes-on is required.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SEAT = "cursor-sdk"
JUDGMENT_MODEL = "cursor/grok-4.6"
JUDGMENT_MODEL_KNOBS: dict[str, str] = {
    "effort": "xhigh",
    "fast": "false",
}
# Compatibility aliases — materializers + tests still import these names.
DEFAULT_MODEL = JUDGMENT_MODEL
DEFAULT_MODEL_KNOBS = JUDGMENT_MODEL_KNOBS
DEFAULT_CONTRACT = "light-bounded"

IMPLEMENT_MODEL = "cursor/composer-2.5"
IMPLEMENT_CONTRACT = "implement"
# Composer exposes exactly one knob (``fast``). Pin true explicitly — do not
# carry Grok ``effort`` onto this bind (align_cursor_knobs drops unrecognized
# knobs silently).
IMPLEMENT_MODEL_KNOBS: dict[str, str] = {"fast": "true"}

# R1 L2: charter-origin write windows refuse silent queue behind a live lease.
_WRITE_LEASE_FENCE: dict[str, bool] = {"refuse_if_lease_held": True}


def default_judgment_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Wire body for ``POST /api/v1/team/dispatch`` (default Grok window).

    ``subject`` is accepted for call-site symmetry with handoff but is **not**
    on the generate schema (handoff-only). WIP subject is posted on the root bus
    pointer, not the Stargate body.
    """
    del subject, window_index  # handoff-only / pointer-side; not generate wire
    return {
        "op": "generate",
        "seat": DEFAULT_SEAT,
        "model": JUDGMENT_MODEL,
        "model_knobs": dict(JUDGMENT_MODEL_KNOBS),
        "contract": DEFAULT_CONTRACT,
        "packet_path": packet_path,
        "dispatch_thread_id": root_id,
        "caller_agent": caller_agent,
        **_WRITE_LEASE_FENCE,
    }


def implement_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
    source_ref: str,
) -> dict[str, Any]:
    """Wire body for a mechanical G4 implement window (Composer, gated).

    ``source_ref`` is required, not optional: under ``contract=implement`` the
    Stargate gate resolves readiness from the packet's front matter and this
    ref, and a dispatch that cannot name a work item must never reach this
    function — ``executor_routing`` fails closed to the judgment bind instead.
    """
    del subject, window_index  # handoff-only / pointer-side; not generate wire
    return {
        "op": "generate",
        "seat": DEFAULT_SEAT,
        "model": IMPLEMENT_MODEL,
        "model_knobs": dict(IMPLEMENT_MODEL_KNOBS),
        "contract": IMPLEMENT_CONTRACT,
        "packet_path": packet_path,
        "dispatch_thread_id": root_id,
        "caller_agent": caller_agent,
        "source_ref": source_ref,
        **_WRITE_LEASE_FENCE,
    }


def autonomous_generate_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Wire body for the autonomous background-lead window.

    Same generate wire as ``default_judgment_body`` (Sonnet 5 @ xhigh).
    The autonomous mandate lives in the materialized packet + root WIP pointer
    — generate schema rejects ``subject`` / ``tags`` (handoff-only fields).
    """
    return default_judgment_body(
        root_id=root_id,
        window_index=window_index,
        packet_path=packet_path,
        subject=subject,
        caller_agent=caller_agent,
    )


def default_handoff_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Wire body for ``POST /api/v1/team/handoff`` (attended IDE consult)."""
    return {
        "op": "handoff",
        "role": "cursor-consult",
        "packet_path": packet_path,
        "subject": subject,
        "caller_agent": caller_agent,
        "tags": [
            f"root:{root_id}",
            f"window:{window_index}",
            "admission:handoff",
            "charter-window",
        ],
    }


def consult_host_generate_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Unattended host wire for consult seats that fire CDP (judgment_gap + r_admit).

    Composer I/O host shell — consult mandate lives in the materialized packet.
    The host owns ``team_dispatch(model=cdp/opus-5)`` submit→poll — auto-wake, no human
    ``push_reminder`` (a:26476 / web-consult on-tick).

    ``read_only=True`` releases the cursor dispatch write lease — the host polls
    CDP and writes cortex provenance only, never checkout edits.
    """
    del subject, window_index
    return {
        "op": "generate",
        "seat": DEFAULT_SEAT,
        "model": IMPLEMENT_MODEL,
        "model_knobs": dict(IMPLEMENT_MODEL_KNOBS),
        "contract": DEFAULT_CONTRACT,
        "packet_path": packet_path,
        "dispatch_thread_id": root_id,
        "caller_agent": caller_agent,
        "read_only": True,
    }


def operator_proxy_host_generate_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Unattended host wire for the CDP operator-proxy lane (agent-bus:6006).

    Composer I/O host shell. The host polls the private ``request`` thread and CDP
    executions and re-admits to keep the operator seat live; ``read_only=True``
    releases the write lease so Opus's own cursor-auto implement dispatches nest
    under this holder rather than contending.
    """
    del subject, window_index
    return {
        "op": "generate",
        "seat": DEFAULT_SEAT,
        "model": IMPLEMENT_MODEL,
        "model_knobs": dict(IMPLEMENT_MODEL_KNOBS),
        "contract": DEFAULT_CONTRACT,
        "packet_path": packet_path,
        "dispatch_thread_id": root_id,
        "caller_agent": caller_agent,
        "read_only": True,
    }


def r_admit_consult_generate_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Alias — R-admit uses the shared consult-host generate wire."""
    return consult_host_generate_body(
        root_id=root_id,
        window_index=window_index,
        packet_path=packet_path,
        subject=subject,
        caller_agent=caller_agent,
    )


def consult_handoff_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Attended-only web-consult handoff (push_reminder). Not used on autonomous tick.

    Autonomous ``judgment_gap`` admits via ``consult_host_generate_body`` so CDP
    auto-wakes. Kept for tests / explicit attended handoff callers.
    """
    return {
        "op": "handoff",
        "role": "web-consult",
        "packet_path": packet_path,
        "subject": subject,
        "caller_agent": caller_agent,
        "tags": [
            f"root:{root_id}",
            f"window:{window_index}",
            "admission:consult",
            "charter-window",
        ],
    }
