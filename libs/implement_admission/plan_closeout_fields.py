"""Structured plan/implement closeout fields (R1 §5 — class vs readiness split)."""

from __future__ import annotations

import re
from typing import Any, Literal

from implement_admission.dense_spec_schema import (
    dense_spec_sha256,
    dense_spec_visible_text,
    validate_dense_spec,
)

ForkKind = Literal["architecture", "design", "locus", "authority"]

_OPEN_LINE_RE = re.compile(r"^\s*OPEN\s*:\s*(.+?)\s*$", re.I | re.M)


def open_fork_entries_from_spec(spec_text: str) -> list[dict[str, Any]]:
    """Union spec OPEN: markers as design forks (live-marker predicate)."""
    visible = dense_spec_visible_text(spec_text)
    entries: list[dict[str, Any]] = []
    for idx, match in enumerate(_OPEN_LINE_RE.finditer(visible)):
        question = match.group(1).strip()
        if not question:
            continue
        entries.append(
            {
                "id": f"spec-open-{idx + 1}",
                "question": question,
                "kind": "design",
                "blocks_implement": True,
            }
        )
    return entries


def merge_open_forks(
    worker_forks: list[dict[str, Any]] | None,
    spec_text: str | None,
) -> list[dict[str, Any]]:
    """Worker-declared forks unioned with spec OPEN: markers."""
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in worker_forks or []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "design")
        question = str(item.get("question") or item.get("id") or "").strip()
        if not question:
            continue
        key = (kind, question)
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(item))
    if spec_text:
        for entry in open_fork_entries_from_spec(spec_text):
            key = ("design", entry["question"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    return merged


def authority_fork_from_open_forks(open_forks: list[dict[str, Any]] | None) -> bool:
    """True iff any fork carries kind=authority."""
    for item in open_forks or []:
        if (
            isinstance(item, dict)
            and str(item.get("kind") or "").strip().lower() == "authority"
        ):
            return True
    return False


def plan_spec_fields(spec_text: str | None) -> tuple[str | None, bool | None]:
    """Return (spec_sha256, dense_spec_valid) for closeout when spec text is known."""
    if not spec_text or not spec_text.strip():
        return None, None
    verdict = validate_dense_spec(spec_text)
    return dense_spec_sha256(spec_text), verdict.passed


__all__ = [
    "authority_fork_from_open_forks",
    "merge_open_forks",
    "open_fork_entries_from_spec",
    "plan_spec_fields",
]
