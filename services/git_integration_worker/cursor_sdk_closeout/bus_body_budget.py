"""MAX_TURN_BODY_CHARS bus invariant and the deterministic oversize-body reduction ladder.

``finalize_closeout_body`` shrinks ImplementCloseout JSON through effects-head
truncation, then summary clipping, then a minimal payload, then a hard slice.
``MAX_TURN_BODY_CHARS`` must stay aligned with ``libs/agent_bus_store/turns_models``.
``_CLOSEOUT_FILE_HEAD`` is the per-list keep count on the first reduction rung.
"""

from __future__ import annotations

import json
from typing import Any

# Must stay aligned with ``libs/agent_bus_store/turns_models`` bus invariants.
MAX_TURN_BODY_CHARS = 8_000
_CLOSEOUT_FILE_HEAD = 5

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
    verification = payload.get("verification") or []
    failed_verification = [
        item
        for item in verification
        if isinstance(item, dict) and item.get("exit_code")
    ]
    if failed_verification:
        reduced["verification"] = failed_verification
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
    if residue:
        minimal["propagation_residue"] = list(residue[:_CLOSEOUT_FILE_HEAD])
    if body_relocated is not None:
        minimal["body_relocated"] = body_relocated
    result = json.dumps(minimal, separators=(",", ":"))
    while len(result) > MAX_TURN_BODY_CHARS and len(minimal["summary"]) > 20:
        minimal["summary"] = str(minimal["summary"])[:-10]
        result = json.dumps(minimal, separators=(",", ":"))
    return result[:MAX_TURN_BODY_CHARS] if len(result) > MAX_TURN_BODY_CHARS else result
