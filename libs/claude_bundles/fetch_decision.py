"""Arrival-inject receipt for a staged fetch.

RULING: operator-seat procedure is in force at the act only as its success
condition, and that sentence lives in the harness-authored arrival inject
(``## This hop``), which exists before the seat's first token. The step list
stays in the runbook or skill and is not copied. A pointer line is not a
receipt. ``resolved`` means the harness hashed bytes (reachable). ``in_context``
means a caller attested the body was loaded. Reachable is not read.

What breaks the circle: ``runbook:maestro-loop`` § Trigger can bind only a
seat that has already read it. This module's success-condition line is
written by the hop composer, which is not waiting on that read.

Falsifier: ``arrival_bind_failure`` returns ``receipt_absent`` when the inject
does not carry a ``fetch-decision`` for each required ref (a lone
``- runbook:`` line fails), or ``success_condition_absent`` when that line
is missing. ``skipped`` satisfies the receipt and ``step_list_in_force``
stays false.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

ARRIVAL_FETCH_REFS: tuple[str, ...] = (
    "runbook:maestro-loop",
    "skill:retrieval-before-authoring",
)

# Success condition of the maestro loop, not its step list. One line so the
# arrival inject does not re-inline the procedure.
HOP_SUCCESS_CONDITION = (
    "- success-condition: the first commission is not `wait`, not "
    "`to=cursor-auto`, not a copied thread `cse_*` identity, not over "
    "2000 characters, and not a propagate while porcelain shows `M`/`D` "
    "on a landed path"
)

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
) -> str:
    """Success condition plus one receipt per ``ARRIVAL_FETCH_REFS`` entry.

    ``in_context`` wins over ``resolved``. Absent bytes and no attestation
    is ``skipped``.
    """
    attested = set(in_context_refs)
    bodies = resolved_bodies or {}
    lines = [HOP_SUCCESS_CONDITION]
    for ref in ARRIVAL_FETCH_REFS:
        if ref in attested:
            lines.append(format_fetch_decision(ref, state="in_context"))
        elif ref in bodies:
            lines.append(format_fetch_decision(ref, state="resolved", body=bodies[ref]))
        else:
            lines.append(
                format_fetch_decision(ref, state="skipped", reason=skip_reason)
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
    """None when the inject carries the success condition and every required receipt.

    A pointer-only ``- runbook:`` line is ``receipt_absent``. ``skipped`` is
    not a failure: the skip is the trace.
    """
    if "- success-condition:" not in (block or ""):
        return "success_condition_absent"
    parsed = {row.ref: row for row in parse_fetch_decisions(block)}
    for ref in ARRIVAL_FETCH_REFS:
        if ref not in parsed:
            return "receipt_absent"
    return None


def step_list_in_force(block: str, ref: str) -> bool:
    """True only when *ref* is ``in_context``. ``resolved`` and ``skipped`` are not."""
    for row in parse_fetch_decisions(block):
        if row.ref == ref and row.state == "in_context":
            return True
    return False


def receipts_present(block: str) -> bool:
    """True when grafting would be a no-op."""
    return arrival_bind_failure(block) is None


__all__ = [
    "ARRIVAL_FETCH_REFS",
    "HOP_SUCCESS_CONDITION",
    "FetchDecision",
    "arrival_bind_failure",
    "decision_lines",
    "format_fetch_decision",
    "parse_fetch_decisions",
    "receipts_present",
    "step_list_in_force",
]
