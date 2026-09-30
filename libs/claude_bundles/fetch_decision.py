"""Arrival-inject receipt for a staged fetch.

Trigger and Refuse are inlined by the hop composer from runbook bytes the
composer read. The Steps section is not copied. ``resolved`` means the harness
hashed reachable bytes. ``in_context`` means a caller attested the body was
loaded. ``skipped`` on ``runbook:maestro-loop`` is ``receipt_unresolved`` for
arrival bind; ``skipped`` on any other ref is still a trace line in the block.
A pointer line is not a receipt.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

ARRIVAL_FETCH_REFS: tuple[str, ...] = (
    "runbook:maestro-loop",
    "skill:retrieval-before-authoring",
)

ARRIVAL_REQUIRED_RESOLVED: tuple[str, ...] = ("runbook:maestro-loop",)

_REASON_RE = re.compile(r"[a-z0-9_]+")
_LINE_RE = re.compile(
    r"^- fetch-decision: (?P<ref>\S+) (?P<state>skipped|resolved|in_context)"
    r"(?: reason=(?P<reason>[a-z0-9_]+)"
    r"| sha256=(?P<sha>[0-9a-f]{64}) bytes=(?P<nbytes>\d+))?\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True, slots=True)
class FetchDecision:
    """One parsed ``fetch-decision`` line."""

    ref: str
    state: str
    reason: str = ""
    sha256: str = ""
    nbytes: int = 0


def success_condition_line(refuse_body: str) -> str:
    """One success-condition bullet derived from the Refuse excerpt body."""
    collapsed = " ".join((refuse_body or "").split())
    if not collapsed:
        return "- success-condition:"
    return f"- success-condition: {collapsed}"


def format_fetch_decision(
    ref: str,
    *,
    state: str,
    reason: str = "",
    body: str | None = None,
) -> str:
    """Return one bullet. ``resolved`` hashes *body*. ``skipped`` needs a reason token."""
    token = ref.strip()
    if not token or any(ch.isspace() for ch in token):
        raise ValueError(f"fetch ref must be one token: {ref!r}")
    if state == "skipped":
        if not _REASON_RE.fullmatch(reason):
            raise ValueError(f"skipped requires a reason token: {reason!r}")
        return f"- fetch-decision: {token} skipped reason={reason}"
    if state == "resolved":
        if body is None:
            raise ValueError("resolved requires body")
        raw = body.encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()
        return f"- fetch-decision: {token} resolved sha256={digest} bytes={len(raw)}"
    if state == "in_context":
        return f"- fetch-decision: {token} in_context"
    raise ValueError(f"unknown fetch state: {state!r}")


def decision_lines(
    *,
    in_context_refs: tuple[str, ...] = (),
    resolved_bodies: dict[str, str] | None = None,
    skip_reason: str = "not_in_context",
    refuse_body: str = "",
    skip_reasons: dict[str, str] | None = None,
) -> str:
    """Success condition plus one receipt per ``ARRIVAL_FETCH_REFS`` entry.

    ``in_context`` wins over ``resolved``. Per-ref skip uses *skip_reasons*
    when present, else *skip_reason*.
    """
    attested = set(in_context_refs)
    bodies = resolved_bodies or {}
    per_ref_skip = skip_reasons or {}
    lines = [success_condition_line(refuse_body)]
    for ref in ARRIVAL_FETCH_REFS:
        if ref in attested:
            lines.append(format_fetch_decision(ref, state="in_context"))
        elif ref in bodies:
            lines.append(format_fetch_decision(ref, state="resolved", body=bodies[ref]))
        else:
            reason = per_ref_skip.get(ref, skip_reason)
            lines.append(
                format_fetch_decision(ref, state="skipped", reason=reason)
            )
    return "\n".join(lines)


def parse_fetch_decisions(text: str) -> tuple[FetchDecision, ...]:
    """Parse every well-formed ``fetch-decision`` bullet. Malformed lines are skipped."""
    found: list[FetchDecision] = []
    for match in _LINE_RE.finditer(text or ""):
        state = match.group("state")
        reason = match.group("reason") or ""
        sha = match.group("sha") or ""
        nbytes_raw = match.group("nbytes")
        if state == "skipped" and not reason:
            continue
        if state == "resolved" and (not sha or nbytes_raw is None):
            continue
        if state == "in_context" and (reason or sha):
            continue
        found.append(
            FetchDecision(
                ref=match.group("ref"),
                state=state,
                reason=reason,
                sha256=sha,
                nbytes=int(nbytes_raw) if nbytes_raw else 0,
            )
        )
    return tuple(found)


def arrival_bind_failure(block: str) -> str | None:
    """None when bind checks pass; else a failure token."""
    if "- success-condition:" not in (block or ""):
        return "success_condition_absent"
    parsed = {row.ref: row for row in parse_fetch_decisions(block)}
    for ref in ARRIVAL_FETCH_REFS:
        if ref not in parsed:
            return "receipt_absent"
    for ref in ARRIVAL_REQUIRED_RESOLVED:
        row = parsed.get(ref)
        if row is not None and row.state == "skipped":
            return "receipt_unresolved"
    return None


def receipts_present(block: str) -> bool:
    """True when the block carries a success condition and every ref is parsed."""
    if "- success-condition:" not in (block or ""):
        return False
    parsed = {row.ref: row for row in parse_fetch_decisions(block)}
    for ref in ARRIVAL_FETCH_REFS:
        if ref not in parsed:
            return False
    return True


__all__ = [
    "ARRIVAL_FETCH_REFS",
    "ARRIVAL_REQUIRED_RESOLVED",
    "FetchDecision",
    "arrival_bind_failure",
    "decision_lines",
    "format_fetch_decision",
    "parse_fetch_decisions",
    "receipts_present",
    "success_condition_line",
]
