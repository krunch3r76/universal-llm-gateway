"""Thin agent-bus thread classification — spine + enrollment only.

Human name for ``spine=root``: **orchestration thread** (``decision:orchestration-thread``).

Spine is ``root`` | ``work`` (tag ``role:root``; default work). Enrollment is
the existing ``charter-runner`` dual-key. Constraint: enrolled ⇒ spine root
(auto-stamp ``role:root``). Only reserved spine tag is ``role:root``; other
``role:*`` tags are rejected on write.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

from agent_bus_store.enrollment_guard import (
    ENROLLMENT_TAG,
    gate_enrollment_tags,
    normalize_tag_list,
)

ROLE_ROOT_TAG = "role:root"
_ROLE_PREFIX = "role:"

Spine = Literal["root", "work"]


class ThreadClassification(TypedDict):
    spine: Spine
    enrolled: bool


class ThreadClassificationError(ValueError):
    """Raised when a write carries an unknown ``role:*`` tag."""

    def __init__(self, *, detail: str, error_code: str = "unknown_role_tag") -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = error_code


def resolve_spine(
    tags: list[str] | None,
    *,
    has_checkpoint_turn: bool = False,
) -> Spine:
    """Return spine class from tags (tag wins).

    ``has_checkpoint_turn`` is a legacy read-path hint only: CHECKPOINT present
    and not ``type:monitor`` ⇒ treat as root until stamped. Never used on write.
    """
    cleaned = normalize_tag_list(tags)
    if ROLE_ROOT_TAG in cleaned:
        return "root"
    if has_checkpoint_turn and "type:monitor" not in cleaned:
        return "root"
    return "work"


def classify_thread(
    tags: list[str] | None,
    *,
    has_checkpoint_turn: bool = False,
) -> ThreadClassification:
    """Read-side helper: spine + enrollment (no DB I/O)."""
    cleaned = normalize_tag_list(tags)
    return {
        "spine": resolve_spine(cleaned, has_checkpoint_turn=has_checkpoint_turn),
        "enrolled": ENROLLMENT_TAG in cleaned,
    }


def gate_thread_tags(
    new_tags: list[str] | None,
    *,
    prior_tags: list[str] | None,
    enroll_charter_runner: bool = False,
) -> list[str]:
    """Normalize + enrollment dual-key + role guard + enrolled⇒root stamp."""
    cleaned = gate_enrollment_tags(
        new_tags,
        prior_tags=prior_tags,
        enroll_charter_runner=enroll_charter_runner,
    )
    unknown = sorted(
        tag
        for tag in cleaned
        if tag.startswith(_ROLE_PREFIX) and tag != ROLE_ROOT_TAG
    )
    if unknown:
        raise ThreadClassificationError(
            detail=(
                f"Unknown role tag(s) {unknown}: only {ROLE_ROOT_TAG!r} is "
                "reserved for the thread spine. Omit other role:* tags."
            )
        )
    if ENROLLMENT_TAG in cleaned and ROLE_ROOT_TAG not in cleaned:
        cleaned = [*cleaned, ROLE_ROOT_TAG]
    return cleaned


def classification_denied_http(
    exc: BaseException,
) -> tuple[int, dict[str, Any]] | None:
    """Map ``ThreadClassificationError`` → (422, detail dict) for FastAPI routes."""
    if not isinstance(exc, ThreadClassificationError):
        return None
    return 422, {"error": exc.error_code, "detail": exc.detail}


__all__ = [
    "ROLE_ROOT_TAG",
    "Spine",
    "ThreadClassification",
    "ThreadClassificationError",
    "classification_denied_http",
    "classify_thread",
    "gate_thread_tags",
    "resolve_spine",
]
