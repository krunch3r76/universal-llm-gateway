"""Verbal tape adapter — LLM-safe {role, content} view of mechanical messages.

Uses ``CORE_KEYS`` from ``continuity_tape.messages``. Index overflow rows live
only in ``index[]`` on the tape route — they never appear in ``tape_verbal``.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from continuity_tape.messages import CORE_KEYS


def project_role_content(message: Mapping[str, Any]) -> dict[str, str]:
    """Return a strict ``{role, content}`` copy; extra keys are dropped."""
    return {
        "role": str(message.get("role") or ""),
        "content": str(message.get("content") or ""),
    }


def project_role_content_list(
    messages: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Map speech messages to role/content copies; omit index-role rows."""
    out: list[dict[str, str]] = []
    for message in messages:
        if str(message.get("role") or "") == "index":
            continue
        verbal = project_role_content(message)
        if set(verbal) <= set(CORE_KEYS):
            out.append(verbal)
    return out


__all__ = ["CORE_KEYS", "project_role_content", "project_role_content_list"]
