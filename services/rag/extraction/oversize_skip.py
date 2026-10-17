"""Detect ``Prompt too large`` map failures for extraction skip policy."""

from __future__ import annotations

import re
from typing import Any

_PROMPT_TOO_LARGE = "Prompt too large"
_ITERATION_INDEX = re.compile(r"Iteration\s+(\d+)")


def is_prompt_too_large(message: str | None) -> bool:
    return bool(message) and _PROMPT_TOO_LARGE in message


def hint_error_by_index(hints: list[Any]) -> dict[int, str]:
    by_index: dict[int, str] = {}
    for hint in hints:
        if not isinstance(hint, dict) or "index" not in hint:
            continue
        by_index[int(hint["index"])] = str(hint.get("error_message") or "")
    return by_index


def oversize_indexes_from_message(message: str, chunk_count: int) -> set[int]:
    """Infer oversize chunk indexes from a MapPartialFailureError message."""
    if chunk_count <= 0:
        return set()

    indexed: set[int] = set()
    for line in message.splitlines():
        if _PROMPT_TOO_LARGE not in line:
            continue
        match = _ITERATION_INDEX.search(line)
        if match is not None:
            indexed.add(int(match.group(1)))

    if indexed:
        return {i for i in indexed if 0 <= i < chunk_count}

    if re.search(r"\b0/1\b", message) and _PROMPT_TOO_LARGE in message:
        return set(range(chunk_count))

    ptl_lines = sum(
        1 for line in message.splitlines() if _PROMPT_TOO_LARGE in line
    )
    if ptl_lines == chunk_count:
        return set(range(chunk_count))

    return set()


def classify_iteration_results(
    iteration_results: list[dict[str, Any]],
) -> tuple[set[int], set[int], bool]:
    """Classify map iteration rows from ``MapPartialFailureError`` data.

    Returns ``(oversize_failed_indexes, completed_indexes, has_non_oversize_failure)``.
    """
    oversize_failed: set[int] = set()
    completed: set[int] = set()
    has_non_oversize_failure = False

    for row in iteration_results:
        if not isinstance(row, dict):
            continue
        index = row.get("index")
        if not isinstance(index, int):
            continue
        status = row.get("status")
        if status == "completed":
            completed.add(index)
            continue
        error_message = row.get("error_message")
        if is_prompt_too_large(
            error_message if isinstance(error_message, str) else None
        ):
            oversize_failed.add(index)
        else:
            has_non_oversize_failure = True

    return oversize_failed, completed, has_non_oversize_failure
