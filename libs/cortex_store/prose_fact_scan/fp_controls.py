"""False-positive controls applied before STALE verdict."""

from __future__ import annotations

import json
import re
from pathlib import PurePosixPath

from .constants import (
    ALIGNMENT_ADMIT_THRESHOLD,
    ANNOTATED_RESOLVED_RE,
    BIND_ADMIT_THRESHOLD,
    CITATION_RE,
    WRONG_FENCED_RE,
)
from .models import FpCounters

CITATION_PATTERN = re.compile(CITATION_RE)
ANNOTATED_PATTERN = re.compile(ANNOTATED_RESOLVED_RE, re.IGNORECASE)
WRONG_PATTERN = re.compile(WRONG_FENCED_RE, re.IGNORECASE)


def _in_wrong_fence(text: str, clause: str) -> bool:
    needle = clause.strip()
    if not needle:
        return False
    parts = text.split("```")
    for idx in range(1, len(parts), 2):
        block = parts[idx]
        if not WRONG_PATTERN.search(block):
            continue
        if needle in block:
            return True
    return False


def _is_protocol_section(path: str, clause: str) -> bool:
    name = PurePosixPath(path).name
    if not name.startswith("operational-context-"):
        return False
    lowered = clause.lower()
    return any(
        token in lowered
        for token in ("protocol", "teaching", "example", "do not read")
    )


def apply_fp_controls(
    *,
    path: str,
    clause: str,
    full_text: str,
    bind_score: float | None,
    alignment_score: float | None,
    counters: FpCounters,
) -> tuple[bool, str | None]:
    """Return (skip_stale, reason)."""
    if CITATION_PATTERN.search(clause):
        counters.citation_skip += 1
        return True, "cited"
    if ANNOTATED_PATTERN.search(clause):
        counters.precorrection_skip += 1
        return True, "precorrection"
    if _in_wrong_fence(full_text, clause):
        counters.wrong_fenced_skip += 1
        return True, "wrong_fenced"
    if _is_protocol_section(path, clause):
        counters.protocol_skip += 1
        return True, "protocol"
    if bind_score is not None and bind_score < BIND_ADMIT_THRESHOLD:
        counters.bind_suppress += 1
        return True, "bind_low"
    if alignment_score is not None and alignment_score >= ALIGNMENT_ADMIT_THRESHOLD:
        counters.alignment_suppress += 1
        return True, "aligned"
    return False, None


def extract_status_tokens(text: str) -> set[str]:
    tokens = {
        "suspended",
        "reinstated",
        "restored",
        "deactivated",
        "active",
        "banned",
        "terminated",
        "employed",
        "onboarded",
    }
    lowered = text.lower()
    return {tok for tok in tokens if tok in lowered}


def parse_events_json(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        if isinstance(parsed, list):
            return [str(x) for x in parsed]
        return [str(parsed)]
    return [str(raw)]
