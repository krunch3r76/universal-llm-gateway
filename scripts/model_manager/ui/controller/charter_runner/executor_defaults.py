"""Charter-runner executor binds — judgment (default) and implement.

Operator bind 2026-07-20: default agent = **Grok 4.5** on the coding
substrate. Wire: ``seat=cursor-sdk``, ``model=cursor/grok-4.5``.

Operator bind 2026-07-26 (iteration speed): both judgment and implement
windows pin ``fast=true``. Grok keeps ``effort=high`` with fast on (not the
prior High = effort=high + fast=false mapping). Composer pins ``fast=true``
explicitly so window_log / admit notes record the bind.

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
DEFAULT_MODEL = "cursor/grok-4.5"
DEFAULT_CONTRACT = "light-bounded"
# Iteration-speed bind (operator 2026-07-26): high effort + fast.
DEFAULT_MODEL_KNOBS: dict[str, str] = {"effort": "high", "fast": "true"}

IMPLEMENT_MODEL = "cursor/composer-2.5"
IMPLEMENT_CONTRACT = "implement"
# Composer exposes exactly one knob (``fast``). Pin true explicitly — do not
# carry Grok ``effort`` onto this bind (align_cursor_knobs drops unrecognized
# knobs silently).
IMPLEMENT_MODEL_KNOBS: dict[str, str] = {"fast": "true"}


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
        "model": DEFAULT_MODEL,
        "model_knobs": dict(DEFAULT_MODEL_KNOBS),
        "contract": DEFAULT_CONTRACT,
        "packet_path": packet_path,
        "dispatch_thread_id": root_id,
        "caller_agent": caller_agent,
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

    Same generate wire as ``default_judgment_body`` (cursor-sdk Grok 4.5 fast).
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


def r_admit_consult_generate_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Unattended generate wire for R-admit consult hosting (project_ask-capable).

    Same cursor-sdk Grok generate schema as worker windows; the R-admit mandate
    lives in the materialized consult packet. Distinct from ``consult_handoff_body``
    (web-consult judgment-gap handoff — no ``project_ask`` on that wire).

    ``read_only=True`` releases the cursor dispatch write lease — the consult seat
    polls CDP and writes cortex provenance only, never checkout edits.
    """
    body = default_judgment_body(
        root_id=root_id,
        window_index=window_index,
        packet_path=packet_path,
        subject=subject,
        caller_agent=caller_agent,
    )
    body["read_only"] = True
    return body


def consult_handoff_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Wire body for charter CONSULT_PENDING pickup (cross-family web-consult)."""
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
