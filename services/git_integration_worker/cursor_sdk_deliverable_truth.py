"""Closeout-truth backstop for light-bounded cursor-sdk dispatch (friction 21654).

Fix #3. Independent of the #1 stream-capture and #2 large-write-path fixes: a
light-bounded dispatch must not report ``status: complete`` when a named
deliverable never landed. Two source-independent signals feed one PARTIAL
degrade decision:

1. Structured — a deliverable-write-shaped tool call (from
   ``SdkRunOutcome.tool_calls``, populated by ``observe_run_stream``) that
   failed/truncated with no successful write to replace it.
2. Stated-intent-no-write — the assistant's terminal body states intent to
   write a durable deliverable but no corresponding write landed. This is the
   tell that let 21654 be hand-diagnosed with zero instrumentation, so it must
   hold even when signal 1 is empty (``.stream()`` unsupported, or the choke is
   upstream of the stream itself).

The output is a ``degraded_reason`` string, consumed by ``_map_closeout_status``
in ``cursor_sdk_closeout`` (any non-``run_status=`` reason maps to PARTIAL).
"""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

LIGHT_BOUNDED_CONTRACT = "light-bounded"

# fs multiplexes read+write under one tool name, so name alone cannot say
# whether a call was a write. It is only used (a) to key the failure signal —
# a truncated/errored fs call is anomalous regardless of read/write — and
# (b) as one half of the content-bearing-write predicate below.
_WRITE_FAMILY_TOOLS = frozenset(
    {
        "fs",
        "write",
        "write_binary",
        "edit",
        "search_replace",
        "apply_patch",
        "str_replace",
        "str_replace_editor",
        "multiedit",
        "create_file",
    }
)

# A durable deliverable write carries its content inline in the call args, so a
# real write dwarfs a read (whose args are just sandbox+op+path, ~<200B). This
# threshold separates a content-bearing write from a path-only read given that
# ToolCallObservation exposes arg_bytes but not the fs ``op``.
_WRITE_CONTENT_MIN_BYTES = 256

# Arg-side truncation is the 21654 signature (large inline content dying in the
# runtime->stdio->fastmcp-remote hop); result-side truncation is a large-read
# concern, not a failed deliverable write.
_ARG_TRUNCATION_FIELDS = frozenset({"args", "arguments", "input", "params", "content"})

_WRITE_INTENT_VERB = (
    r"(?:writ(?:e|es|ing|ten)|wrote|sav(?:e|es|ing|ed)|"
    r"persist(?:s|ed|ing)?|creat(?:e|es|ing|ed)|generat(?:e|es|ing|ed))"
)
_DURABLE_TARGET = (
    r"(?:cortex://|workspaces://|fs\s*\(|"
    r"(?:notes/system/|tasks/|docs/|libs/|services/|config/|scripts/|pipelines/)"
    r"[\w./-]+|"
    r"[\w./-]+\.(?:md|json|ya?ml|txt|csv|html|py))"
)
_VERB_RE = re.compile(_WRITE_INTENT_VERB, re.IGNORECASE)
_TARGET_RE = re.compile(_DURABLE_TARGET, re.IGNORECASE)
_INTENT_WINDOW = 160


def _is_write_family_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower()
    return any(
        name == tool or name.endswith(sep + tool)
        for tool in _WRITE_FAMILY_TOOLS
        for sep in ("_", "-", ".", ":", "/")
    )


def _completed_deliverable_write(tc: ToolCallObservation) -> bool:
    """A write-family call that landed a content-bearing payload cleanly."""
    return (
        _is_write_family_tool(tc.tool_name)
        and tc.status == "completed"
        and not tc.truncated_any
        and tc.arg_bytes >= _WRITE_CONTENT_MIN_BYTES
    )


def _failed_write_family(tc: ToolCallObservation) -> bool:
    if not _is_write_family_tool(tc.tool_name):
        return False
    if tc.status == "error":
        return True
    return bool(_ARG_TRUNCATION_FIELDS.intersection(tc.truncated_fields))


def _landed_deliverable_write(tool_calls: tuple[ToolCallObservation, ...]) -> bool:
    return any(_completed_deliverable_write(tc) for tc in tool_calls)


def _body_states_write_intent(body: str) -> bool:
    text = body or ""
    for match in _VERB_RE.finditer(text):
        window = text[
            max(0, match.start() - _INTENT_WINDOW) : match.end() + _INTENT_WINDOW
        ]
        if _TARGET_RE.search(window):
            return True
    return False


def deliverable_write_choke_reason(
    tool_calls: tuple[ToolCallObservation, ...],
) -> str | None:
    """Structured signal: a write-family call failed/truncated and none landed."""
    if _landed_deliverable_write(tool_calls):
        return None
    if any(_failed_write_family(tc) for tc in tool_calls):
        return "deliverable_write_choked"
    return None


def stated_intent_no_write_reason(
    body: str,
    tool_calls: tuple[ToolCallObservation, ...],
) -> str | None:
    """Source-independent signal: body claims a durable write that never landed.

    Robust to an empty ``tool_calls`` (stream unavailable / choke upstream of the
    stream) — the intent tell is read from the body alone.
    """
    if not _body_states_write_intent(body):
        return None
    if _landed_deliverable_write(tool_calls):
        return None
    return "stated_intent_no_write"


def light_bounded_deliverable_reason(
    *,
    body: str,
    tool_calls: tuple[ToolCallObservation, ...],
    contract: str,
) -> str | None:
    """Degrade reason (→ PARTIAL) when a light-bounded deliverable did not land.

    Contract-gated: other contract types carry their own closeout semantics
    (implement has git-baseline capture + files_expected; consult expects no
    durable artifact), so this backstop applies to ``light-bounded`` only.
    """
    if contract != LIGHT_BOUNDED_CONTRACT:
        return None
    return deliverable_write_choke_reason(tool_calls) or stated_intent_no_write_reason(
        body, tool_calls
    )
