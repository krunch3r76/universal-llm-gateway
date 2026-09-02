"""Charter-runner executor binds — judgment (default) and implement.

Judgment and implement windows both run on the cursor-sdk seat with
``cursor/composer-2.5`` (operator ruling 2026-09-02, superseding the same-day
Sonnet-5 bind — ``decision:grok-4-6-fleet-default`` a:31939 amended). Composer
has one knob (``fast``); there is no ``effort``/``thinking``/``context`` knob to
carry, and ``align_cursor_knobs`` drops unrecognized knobs silently.

The judgment/implement split now lives in ``contract``, not ``model``:
``JUDGMENT_MODEL`` dispatches at ``DEFAULT_CONTRACT`` (``light-bounded``), which
GIW ``resolve_prompt_preamble`` auto-scaffolds with ``Use the reasoning-posture
skill`` + ``Use the hypothesize-simulate skill`` on every cursor-sdk generate —
Composer's reasoning space is squeezed via those two skills rather than by a
heavier model. Hard reasoning gaps are caught by the fleet's extensive external
CDP (Fable/Opus) consultation elsewhere, not by this in-seat default.
``IMPLEMENT_MODEL`` dispatches at ``IMPLEMENT_CONTRACT`` (``implement``) — no
skill scaffolding, mechanical execution against a pre-densified packet.

``cursor/grok-4.6`` is an explicit pin only — path-sim A, ``role=skeptic``, and
family-cross checks — never this module's default. Layer-arc G3 keeps its own
family-diversity locus in ``window_exec.materializer_layer``.

This module's locus is independent of the GIW Auto-lane's own ``wire_map``
resolution (``config/agents.yaml`` judgment-contract comments describe that
separate mechanism) — the two may legitimately diverge; charter_runner is
decommissioned (a:31919), so this bind is inert today regardless.

Step overrides (not this module): CDP Opus for Opus-class code review;
attended Composer handoff when eyes-on is required.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SEAT = "cursor-sdk"
JUDGMENT_MODEL = "cursor/composer-2.5"
JUDGMENT_MODEL_KNOBS: dict[str, str] = {"fast": "true"}
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
    """Wire body for ``POST /api/v1/team/dispatch`` (default judgment window).

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

    Same generate wire as ``default_judgment_body`` (``JUDGMENT_MODEL`` @ ``JUDGMENT_MODEL_KNOBS``).
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
