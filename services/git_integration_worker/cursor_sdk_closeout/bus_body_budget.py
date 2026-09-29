"""MAX_TURN_BODY_CHARS bus invariant and the deterministic oversize-body reduction ladder.

``finalize_closeout_body`` shrinks ImplementCloseout JSON through effects-head
truncation, then summary clipping, then a minimal payload, then whole-key shedding.
``MAX_TURN_BODY_CHARS`` must stay aligned with ``libs/agent_bus_store/turns_models``.
``_CLOSEOUT_FILE_HEAD`` is the per-list keep count on the first reduction rung.
"""

from __future__ import annotations

import json
from typing import Any

# Must stay aligned with ``libs/agent_bus_store/turns_models`` bus invariants.
MAX_TURN_BODY_CHARS = 8_000
_CLOSEOUT_FILE_HEAD = 5
# Bus-body tails. Exit 0 is falsy in Python; a truthiness filter dropped every
# passing row, and an unattributed row stores the integer on wrapper_exit_code
# with exit_code null. The reduced body must still carry both, plus the stream
# tail a reader uses to disposition the check.
_BUS_STREAM_TAIL = 240
_BUS_COMMAND_TAIL = 240
# Keys dropped from the minimal payload, in order, when JSON still exceeds the bus limit.
_MINIMAL_SHED_KEYS = (
    "propagation",
    "propagation_residue",
    "verification",
    "evidence_uris",
)


def _shrink_minimal_summary_until_fits(minimal: dict[str, Any]) -> str:
    """Clip ``minimal["summary"]`` and re-dump until the body fits or summary is exhausted."""
    result = json.dumps(minimal, separators=(",", ":"))
    summary = str(minimal.get("summary", ""))
    while len(result) > MAX_TURN_BODY_CHARS and len(summary) > 20:
        summary = summary[:-10]
        minimal["summary"] = summary
        result = json.dumps(minimal, separators=(",", ":"))
    return result


def _fit_minimal_body(minimal: dict[str, Any]) -> str:
    """Return valid JSON for the minimal closeout floor within ``MAX_TURN_BODY_CHARS``.

    Sheds bulky optional keys in ``_MINIMAL_SHED_KEYS`` order, then clips summary again.
    Never returns a character prefix of JSON — the bus reply must always parse.
    """
    result = _shrink_minimal_summary_until_fits(minimal)
    for key in _MINIMAL_SHED_KEYS:
        if len(result) <= MAX_TURN_BODY_CHARS:
            return result
        if key in minimal:
            del minimal[key]
            result = json.dumps(minimal, separators=(",", ":"))
    result = _shrink_minimal_summary_until_fits(minimal)
    if len(result) > MAX_TURN_BODY_CHARS:
        minimal["summary"] = ""
        result = json.dumps(minimal, separators=(",", ":"))
    return result


def _verification_row_kept(item: object) -> bool:
    """True when a shrink must keep the row.

    ``exit_code`` 0 and ``None`` are both falsy. Passing rows and unattributed
    rows (integer on ``wrapper_exit_code``) are the rows a reader needs.
    """
    if not isinstance(item, dict):
        return False
    if item.get("exit_code") is not None:
        return True
    if item.get("wrapper_exit_code") is not None:
        return True
    return bool(item.get("stdout") or item.get("stderr"))


def _tail_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) <= limit:
        return value
    return "...[truncated]\n" + value[-limit:]


def _compact_verification_row(item: dict[str, Any]) -> dict[str, Any]:
    stdout = item.get("stdout")
    stderr = item.get("stderr")
    command = item.get("command")
    cut = (
        (isinstance(stdout, str) and len(stdout) > _BUS_STREAM_TAIL)
        or (isinstance(stderr, str) and len(stderr) > _BUS_STREAM_TAIL)
        or (isinstance(command, str) and len(command) > _BUS_COMMAND_TAIL)
    )
    return {
        "command": (
            _tail_text(command, _BUS_COMMAND_TAIL)
            if isinstance(command, str)
            else command
        ),
        "exit_code": item.get("exit_code"),
        "wrapper_exit_code": item.get("wrapper_exit_code"),
        "exit_code_register": item.get("exit_code_register"),
        "invocation_id": item.get("invocation_id"),
        "basis": item.get("basis"),
        "stdout": _tail_text(stdout, _BUS_STREAM_TAIL)
        if isinstance(stdout, str)
        else stdout,
        "stderr": _tail_text(stderr, _BUS_STREAM_TAIL)
        if isinstance(stderr, str)
        else stderr,
        "output_truncated": bool(item.get("output_truncated")) or cut,
    }


def _compact_verification(payload: dict[str, Any]) -> list[dict[str, Any]]:
    verification = payload.get("verification") or []
    return [
        _compact_verification_row(item)
        for item in verification
        if _verification_row_kept(item)
    ]


def finalize_closeout_body(
    body: str,
    *,
    body_relocated: dict[str, Any] | None = None,
) -> str:
    """Deterministically shrink an oversize closeout JSON body to the bus limit."""
    if len(body) <= MAX_TURN_BODY_CHARS:
        return body

    payload = json.loads(body)
    reduced: dict[str, Any] = {
        "schema_version": payload.get("schema_version", 1),
        "status": payload["status"],
        "summary": payload["summary"],
        "source_ref": payload["source_ref"],
    }
    if payload.get("work_outcome") is not None:
        reduced["work_outcome"] = payload["work_outcome"]
    if payload.get("status_incomplete_class") is not None:
        reduced["status_incomplete_class"] = payload["status_incomplete_class"]
    if payload.get("capture_status") is not None:
        reduced["capture_status"] = payload["capture_status"]
    if payload.get("evidence_uris"):
        reduced["evidence_uris"] = payload["evidence_uris"]
    kept_verification = _compact_verification(payload)
    if kept_verification:
        reduced["verification"] = kept_verification
    effects = payload.get("effects")
    if effects is not None:
        reduced["effects_total"] = len(effects)
        reduced["effects"] = list(effects[:_CLOSEOUT_FILE_HEAD])
    for field, total_field in (
        ("files_created", "files_created_total"),
        ("files_modified", "files_modified_total"),
        ("files_deleted", "files_deleted_total"),
        ("files_outside_repo", "files_outside_repo_total"),
    ):
        files = payload.get(field) or []
        reduced[total_field] = len(files)
        reduced[field] = list(files[:_CLOSEOUT_FILE_HEAD])
    ignored = payload.get("files_untracked_or_ignored") or []
    if ignored:
        reduced["files_untracked_or_ignored_total"] = len(ignored)
        reduced["files_untracked_or_ignored"] = list(ignored[:_CLOSEOUT_FILE_HEAD])
    offgit = payload.get("files_offgit_produced") or []
    if offgit:
        reduced["files_offgit_produced_total"] = len(offgit)
        reduced["files_offgit_produced"] = list(offgit[:_CLOSEOUT_FILE_HEAD])
    dropped = payload.get("dropped_non_file_entries") or []
    if dropped:
        reduced["dropped_non_file_entries_total"] = len(dropped)
        reduced["dropped_non_file_entries"] = list(dropped[:_CLOSEOUT_FILE_HEAD])
    if payload.get("deviations"):
        reduced["deviations"] = list(payload["deviations"][:_CLOSEOUT_FILE_HEAD])
    residue = payload.get("propagation_residue") or []
    if residue:
        reduced["propagation_residue"] = list(residue[:_CLOSEOUT_FILE_HEAD])
    propagation = payload.get("propagation") or []
    if propagation:
        reduced["propagation"] = list(propagation[:_CLOSEOUT_FILE_HEAD])
    if body_relocated is not None:
        reduced["body_relocated"] = body_relocated

    result = json.dumps(reduced, separators=(",", ":"))
    if len(result) <= MAX_TURN_BODY_CHARS:
        return result

    summary = str(reduced["summary"])
    overhead = len(result) - len(summary)
    max_summary = max(40, MAX_TURN_BODY_CHARS - overhead - 3)
    reduced["summary"] = summary[:max_summary] + "..."
    result = json.dumps(reduced, separators=(",", ":"))
    if len(result) <= MAX_TURN_BODY_CHARS:
        return result

    minimal: dict[str, Any] = {
        "schema_version": 1,
        "status": payload["status"],
        "summary": str(payload["summary"])[:200],
        "evidence_uris": payload.get("evidence_uris"),
    }
    if payload.get("work_outcome") is not None:
        minimal["work_outcome"] = payload["work_outcome"]
    if payload.get("status_incomplete_class") is not None:
        minimal["status_incomplete_class"] = payload["status_incomplete_class"]
    if kept_verification:
        minimal["verification"] = kept_verification
    if residue:
        minimal["propagation_residue"] = list(residue[:_CLOSEOUT_FILE_HEAD])
    if propagation:
        minimal["propagation"] = list(propagation[:_CLOSEOUT_FILE_HEAD])
    if body_relocated is not None:
        minimal["body_relocated"] = body_relocated
    return _fit_minimal_body(minimal)
