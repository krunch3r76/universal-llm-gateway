"""Charter-runner default executor bind.

Operator bind 2026-07-20: default agent = **Grok 4.5 High** on the coding
substrate. Wire: ``seat=cursor-sdk``, ``model=cursor/grok-4.5``.

Grok exposes ``effort`` + ``fast`` only (no ``thinking`` knob — live
ListModels / ``cursor_capabilities``). \"High effort thinking\" maps to
``effort=high`` + ``fast=false``.

Step overrides (not this module): CDP Opus for Opus-class code review;
attended Composer handoff when eyes-on is required.
"""

from __future__ import annotations

from typing import Any

DEFAULT_SEAT = "cursor-sdk"
DEFAULT_MODEL = "cursor/grok-4.5"
DEFAULT_CONTRACT = "light-bounded"
# \"thinking\" is not a Grok knob — non-fast + high effort is the High tier.
DEFAULT_MODEL_KNOBS: dict[str, str] = {"effort": "high", "fast": "false"}


def default_generate_body(
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


def autonomous_generate_body(
    *,
    root_id: str,
    window_index: int,
    packet_path: str,
    subject: str,
    caller_agent: str,
) -> dict[str, Any]:
    """Wire body for the autonomous background-lead window.

    Same generate wire as ``default_generate_body`` (cursor-sdk Grok 4.5 High).
    The autonomous mandate lives in the materialized packet + root WIP pointer
    — generate schema rejects ``subject`` / ``tags`` (handoff-only fields).
    """
    return default_generate_body(
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
            "charter-runner",
            f"root:{root_id}",
            f"window:{window_index}",
            "admission:handoff",
        ],
    }
