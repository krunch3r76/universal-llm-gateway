"""Post-window Frictions audit — parse, verify, reverse check, classify.

Pure logic with injected ``assertion_get`` / ``frictions`` for unit tests.
Does not extend ``checkpoint_parse.ParsedCheckpoint`` (5812 partition).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .checkpoint_parse import _NONE_WINDOW_RE

_FILED_ASSERTION_RE = re.compile(
    r"^\s*[-*]\s*\[filed\s+assertion:(\d+)\]\s*([^:]+):\s*(.+)$",
    re.IGNORECASE,
)
_FRICTIONS_HEADER_RE = re.compile(r"^##\s+Frictions\s*$", re.IGNORECASE | re.MULTILINE)

NOT_APPLICABLE_CLOSEOUT = frozenset({"failed", "timeout"})
NOT_APPLICABLE_CHECKPOINT_PREFIXES = (
    "CHECKPOINT — self-heal",
    "CHECKPOINT — consult-stall",
)


@dataclass(frozen=True)
class FrictionRow:
    assertion_id: int
    category: str
    note: str
    raw: str


@dataclass
class FrictionsAuditResult:
    applicable: bool
    not_applicable_reason: str | None = None
    section_class: str = "missing_section"
    rows: list[FrictionRow] = field(default_factory=list)
    cited_ids: set[int] = field(default_factory=set)
    filed_ids: set[int] = field(default_factory=set)
    uncited_ids: set[int] = field(default_factory=set)
    phantom_ids: set[int] = field(default_factory=set)
    unresolved_ids: set[int] = field(default_factory=set)
    malformed_rows: list[str] = field(default_factory=list)
    ceremonial_suspected: bool = False
    audit_failed: bool = False
    audit_failure_class: str | None = None
    non_actionable_rate: float = 0.0
    resolved_actionable_rows: list[FrictionRow] = field(default_factory=list)
    enqueued_ids: set[int] = field(default_factory=set)


def extract_frictions_section(body: str) -> str | None:
    """Return ``## Frictions`` body text, or ``None`` if section absent."""
    match = _FRICTIONS_HEADER_RE.search(body or "")
    if not match:
        return None
    start = match.end()
    rest = body[start:]
    next_header = re.search(r"^##\s+", rest, re.MULTILINE)
    chunk = rest[: next_header.start()] if next_header else rest
    return chunk.strip()


def parse_frictions_section(section: str | None) -> tuple[str, list[FrictionRow], list[str]]:
    """Classify section: silence | rows | missing_section | malformed."""
    if section is None:
        return "missing_section", [], []
    text = section.strip()
    if not text:
        return "missing_section", [], []
    if _NONE_WINDOW_RE.match(text):
        return "silence", [], []

    rows: list[FrictionRow] = []
    malformed: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _FILED_ASSERTION_RE.match(stripped)
        if not m:
            malformed.append(stripped)
            continue
        rows.append(
            FrictionRow(
                assertion_id=int(m.group(1)),
                category=m.group(2).strip(),
                note=m.group(3).strip(),
                raw=stripped,
            )
        )
    if malformed and not rows:
        return "malformed", rows, malformed
    if malformed:
        return "malformed", rows, malformed
    if not rows:
        return "malformed", rows, malformed
    return "rows", rows, malformed


def audit_applicable(
    *,
    worker_closeout_status: str | None,
    checkpoint_subject: str,
    worker_closed: bool | None,
) -> tuple[bool, str | None]:
    """F8 — audit only after normal terminal completion."""
    if worker_closeout_status in NOT_APPLICABLE_CLOSEOUT:
        return False, f"closeout_{worker_closeout_status}"
    if worker_closed is False:
        return False, "worker_close_failed"
    subj = str(checkpoint_subject or "").strip()
    for prefix in NOT_APPLICABLE_CHECKPOINT_PREFIXES:
        if subj.startswith(prefix):
            return False, "machine_recovery_checkpoint"
    return True, None


def _assertion_charter_root(attrs: dict[str, Any]) -> str | None:
    raw = attrs.get("charter_root")
    if raw is None:
        return None
    text = str(raw).strip()
    if text.lower().startswith("agent-bus:"):
        return text.split(":", 1)[1].strip()
    return text


def _row_actionable(assertion: dict[str, Any]) -> bool:
    attrs = assertion.get("attributes") or {}
    if not isinstance(attrs, dict):
        return True
    return attrs.get("actionable", True) is not False


def _assertion_still_open(assertion: dict[str, Any]) -> bool:
    """False after friction_close / supersede (valid_until or superseded_by set)."""
    if assertion.get("superseded_by") is not None:
        return False
    if assertion.get("valid_until"):
        return False
    return True


# Capture/pipeline closeout tokens are SDK harness noise — not worker material
# defects. Treating them as defect_signal mints false ceremonial_suspected
# repair todos when Frictions correctly says silence (6110 w1–w3).
_NOISE_DEVIATION_EXACT = frozenset(
    {
        "gate:implement_source_ref_unresolved",
        "stream_only_effect",
    }
)
_NOISE_DEVIATION_PREFIXES = ("capture:", "degraded:")


def is_material_deviation(token: str) -> bool:
    """True when a closeout deviation token indicates a material worker defect."""
    text = str(token or "").strip()
    if not text or text in _NOISE_DEVIATION_EXACT:
        return False
    return not any(text.startswith(prefix) for prefix in _NOISE_DEVIATION_PREFIXES)


def defect_signal(
    *,
    gate_bypass_count: int = 0,
    closeout_deviations: list[str] | None = None,
    worker_closeout_status: str | None = None,
) -> bool:
    """Heuristic input for ceremonial Frictions detection."""
    if gate_bypass_count > 0:
        return True
    if worker_closeout_status == "partial":
        return True
    return any(is_material_deviation(d) for d in (closeout_deviations or []))


def closeout_deviations(worker_turns: list[dict[str, Any]]) -> list[str]:
    """Collect deviation tokens from the latest JSON closeout turn."""
    import json

    for turn in reversed(worker_turns or []):
        body = str(turn.get("body") or "").strip()
        if not body.startswith("{"):
            continue
        try:
            data = json.loads(body)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            devs = data.get("deviations")
            if isinstance(devs, list):
                return [str(d) for d in devs]
    return []


def audit_window_frictions(
    *,
    checkpoint_body: str,
    root_id: str,
    window_index: int,
    assertion_get: Callable[[int], dict[str, Any]],
    frictions: Callable[..., dict[str, Any]],
    worker_closeout_status: str | None = None,
    checkpoint_subject: str = "",
    worker_closed: bool | None = None,
    gate_bypass_count: int = 0,
    worker_turns: list[dict[str, Any]] | None = None,
) -> FrictionsAuditResult:
    """Forward + reverse Frictions audit for one harvested window."""
    applicable, na_reason = audit_applicable(
        worker_closeout_status=worker_closeout_status,
        checkpoint_subject=checkpoint_subject,
        worker_closed=worker_closed,
    )
    result = FrictionsAuditResult(applicable=applicable, not_applicable_reason=na_reason)
    if not applicable:
        return result

    section = extract_frictions_section(checkpoint_body)
    section_class, rows, malformed = parse_frictions_section(section)
    result.section_class = section_class
    result.rows = rows
    result.malformed_rows = malformed

    root_digits = root_id
    if root_digits.lower().startswith("agent-bus:"):
        root_digits = root_digits.split(":", 1)[1].strip()

    # Citations that resolve to this root via assertion_get (including same-window
    # closed/superseded rows). Live frictions() defaults to superseded=False, so a
    # filed-then-closed citation would otherwise look like a phantom (6110-w4).
    resolved_same_root_ids: set[int] = set()
    for row in rows:
        result.cited_ids.add(row.assertion_id)
        got = assertion_get(row.assertion_id)
        if "error" in got:
            result.unresolved_ids.add(row.assertion_id)
            continue
        attrs = got.get("attributes") or {}
        if not isinstance(attrs, dict):
            attrs = {}
        filed_root = _assertion_charter_root(attrs)
        if filed_root and filed_root != root_digits:
            result.unresolved_ids.add(row.assertion_id)
            continue
        if filed_root == root_digits:
            resolved_same_root_ids.add(row.assertion_id)
        if _row_actionable(got) and _assertion_still_open(got):
            result.resolved_actionable_rows.append(row)

    filed_resp = frictions(
        charter_root=root_digits,
        window_index=window_index,
        superseded=False,
        limit=100,
        intent="full",
    )
    for item in filed_resp.get("items") or []:
        if isinstance(item, dict) and item.get("id") is not None:
            result.filed_ids.add(int(item["id"]))

    result.uncited_ids = result.filed_ids - result.cited_ids
    result.phantom_ids = (
        result.cited_ids - result.filed_ids - resolved_same_root_ids
    )

    non_actionable = 0
    total_filed = len(result.filed_ids)
    if total_filed:
        for fid in result.filed_ids:
            got = assertion_get(fid)
            if "error" not in got and not _row_actionable(got):
                non_actionable += 1
        result.non_actionable_rate = non_actionable / total_filed

    has_defect = defect_signal(
        gate_bypass_count=gate_bypass_count,
        closeout_deviations=closeout_deviations(worker_turns or []),
        worker_closeout_status=worker_closeout_status,
    )
    all_non_actionable = bool(rows) and not result.resolved_actionable_rows
    if has_defect and (section_class == "silence" or all_non_actionable):
        result.ceremonial_suspected = True

    if section_class == "missing_section":
        result.audit_failed = True
        result.audit_failure_class = "missing_section"
    elif section_class == "malformed":
        result.audit_failed = True
        result.audit_failure_class = "malformed"
    elif result.unresolved_ids or result.phantom_ids:
        result.audit_failed = True
        result.audit_failure_class = "unresolved_id"
    elif result.uncited_ids:
        uncited_actionable = [
            fid
            for fid in result.uncited_ids
            if "error" not in (a := assertion_get(fid)) and _row_actionable(a)
        ]
        if uncited_actionable:
            result.audit_failure_class = "filed_uncited"
    elif result.ceremonial_suspected:
        result.audit_failed = True
        result.audit_failure_class = "ceremonial_suspected"

    return result


__all__ = [
    "FrictionRow",
    "FrictionsAuditResult",
    "audit_applicable",
    "audit_window_frictions",
    "closeout_deviations",
    "defect_signal",
    "extract_frictions_section",
    "is_material_deviation",
    "parse_frictions_section",
]
