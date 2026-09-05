"""Closeout-truth backstop for residual cursor-sdk dispatch (friction 21654).

Fix #3. Independent of the #1 stream-capture and #2 large-write-path fixes: a
residual dispatch (no wt baseline, not implement/wrap) must not report
``status: complete`` when a named deliverable never landed.
"""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_sdk_stream_capture import (
    ToolCallObservation,
)

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

_WRITE_CONTENT_MIN_BYTES = 256

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


def residual_deliverable_applies(
    *,
    contract: str,
    wt_baseline: str | None,
) -> bool:
    """True when the deliverable-land backstop applies to this closeout."""
    wire = (contract or "").strip().lower()
    return wt_baseline is None and wire not in {"implement", "wrap"}


def _is_write_family_tool(tool_name: str) -> bool:
    name = (tool_name or "").lower()
    return any(
        name == tool or name.endswith(sep + tool)
        for tool in _WRITE_FAMILY_TOOLS
        for sep in ("_", "-", ".", ":", "/")
    )


def _completed_deliverable_write(tc: ToolCallObservation) -> bool:
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
    if _landed_deliverable_write(tool_calls):
        return None
    if any(_failed_write_family(tc) for tc in tool_calls):
        return "deliverable_write_choked"
    return None


def stated_intent_no_write_reason(
    body: str,
    tool_calls: tuple[ToolCallObservation, ...],
) -> str | None:
    if not _body_states_write_intent(body):
        return None
    if _landed_deliverable_write(tool_calls):
        return None
    return "stated_intent_no_write"


def residual_deliverable_reason(
    *,
    body: str,
    tool_calls: tuple[ToolCallObservation, ...],
    contract: str,
    wt_baseline: str | None = None,
    deliverable_present: bool = False,
) -> str | None:
    """Degrade reason (→ PARTIAL) when a residual deliverable did not land."""
    if not residual_deliverable_applies(contract=contract, wt_baseline=wt_baseline):
        return None
    if deliverable_present:
        return None
    return deliverable_write_choke_reason(tool_calls) or stated_intent_no_write_reason(
        body, tool_calls
    )
