"""Citation gate for resume fence — scan send bodies against manifest allowlist."""

from __future__ import annotations

import json
import re
from typing import Any

from .events.resume_fence import emit_resume_fence_citation_refused
from .resume_fence_store import fold_fence, release_fence

_BUS_CITE_RE = re.compile(r"agent-bus:(\d{3,6})(?:#(\d+))?")
_CORTEX_URI_RE = re.compile(r"cortex://[^\s)\]>`]+")
_ENTITY_RE = re.compile(
    r"\b(?:todo|decision|document|transcript):[^\s)\]>]+",
    re.IGNORECASE,
)
_GIT_SHA_RE = re.compile(r"\b(?:git:)?([0-9a-f]{7,40})\b", re.IGNORECASE)


def _load_bundle_read_set(fence_id: str) -> dict[str, Any] | None:
    """Re-read manifest from latest poured event payload when cached."""
    from .resume_fence_store import _rows_for_fence

    rows = _rows_for_fence(fence_id)
    for row in reversed(rows):
        if row["event"] != "poured":
            continue
        raw = row.get("payload_json")
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "read_set" in payload:
            return payload["read_set"]
    return None


def _allowed_sets(read_set: dict[str, Any]) -> tuple[set[str], set[str]]:
    readable = read_set.get("readable") or {}
    citable = read_set.get("citable") or {}
    allowed: set[str] = set()
    for tid in readable.get("bus_threads") or []:
        allowed.add(f"agent-bus:{tid}")
        allowed.add(str(tid))
    for turn in readable.get("bus_turns") or []:
        allowed.add(f"agent-bus:{turn}" if "#" not in str(turn) else str(turn))
        if "#" in str(turn):
            allowed.add(str(turn).replace("#", ":"))
    for uri in readable.get("cortex_uris") or []:
        allowed.add(str(uri))
    for ent in readable.get("entities") or []:
        allowed.add(str(ent))
    for tid in citable.get("bus_threads") or []:
        allowed.add(f"agent-bus:{tid}")
        allowed.add(str(tid))
    for sha in citable.get("git") or []:
        allowed.add(str(sha))
        allowed.add(f"git:{sha}")
    for tid in citable.get("transcripts") or []:
        allowed.add(str(tid))
    return allowed, allowed


def extract_citations(body: str) -> list[str]:
    """Extract id-shaped tokens from *body* for allowlist comparison."""
    found: list[str] = []
    for match in _BUS_CITE_RE.finditer(body):
        if match.group(2):
            found.append(f"agent-bus:{match.group(1)}#{match.group(2)}")
        found.append(f"agent-bus:{match.group(1)}")
    found.extend(_CORTEX_URI_RE.findall(body))
    found.extend(_ENTITY_RE.findall(body))
    for match in _GIT_SHA_RE.finditer(body):
        found.append(match.group(1).lower())
        found.append(f"git:{match.group(1).lower()}")
    return found


def check_send_citation_gate(
    *,
    thread_id: str,
    from_agent: str,
    subject: str,
    body: str,
    fence_id: str | None,
) -> dict[str, Any] | None:
    """Return 422 detail dict when citation gate refuses, else None."""
    from .resume_fence_store import find_open_fence_for_agent

    effective_fence_id = fence_id
    if not effective_fence_id:
        effective_fence_id = find_open_fence_for_agent(
            root_thread=thread_id,
            from_agent=from_agent,
        )
        if effective_fence_id:
            return {
                "error": "resume_fence.fence_id_required",
                "reason": "resume_fence.fence_id_required",
                "fence_id": effective_fence_id,
                "thread": thread_id,
                "from_agent": from_agent,
                "message": (
                    "Open resume fence requires fence_id on send for citation gate"
                ),
            }
        return None

    folded = fold_fence(effective_fence_id)
    if folded is None:
        return {
            "error": "resume_fence.not_found",
            "reason": "resume_fence.not_found",
            "fence_id": effective_fence_id,
        }
    if folded.state not in {"armed", "poured"}:
        return {
            "error": "resume_fence.not_open",
            "reason": "resume_fence.not_open",
            "fence_id": effective_fence_id,
            "state": folded.state,
        }
    if folded.root_thread != thread_id:
        return {
            "error": "resume_fence.thread_mismatch",
            "reason": "resume_fence.thread_mismatch",
            "fence_id": effective_fence_id,
            "expected_thread": folded.root_thread,
            "provided_thread": thread_id,
        }

    read_set = _load_bundle_read_set(effective_fence_id)
    if read_set is None:
        from .resume_fence import assemble_resume_fence

        bundle = assemble_resume_fence(
            thread_id,
            transcript_id=folded.transcript_id,
            source="send_gate_rebuild",
        )
        read_set = (bundle.get("read_set") or {}) if not bundle.get("error") else {}

    allowed, _ = _allowed_sets(read_set)
    citations = extract_citations(body)
    foreign = sorted({c for c in citations if c not in allowed})
    if foreign:
        emit_resume_fence_citation_refused(
            fence_id=effective_fence_id,
            foreign=foreign,
            turn_subject=subject,
        )
        return {
            "error": "resume_fence.foreign_citation",
            "reason": "resume_fence.foreign_citation",
            "fence_id": effective_fence_id,
            "foreign": foreign,
            "allowed_counts": {
                "readable_bus_threads": len(
                    (read_set.get("readable") or {}).get("bus_threads") or []
                ),
                "citable_bus_threads": len(
                    (read_set.get("citable") or {}).get("bus_threads") or []
                ),
            },
        }
    return None


def release_on_clean_send(*, fence_id: str, release_turn: int) -> None:
    """Journal release after a citation-clean send."""
    release_fence(fence_id=fence_id, release_turn=release_turn)


__all__ = [
    "check_send_citation_gate",
    "extract_citations",
    "release_on_clean_send",
]
