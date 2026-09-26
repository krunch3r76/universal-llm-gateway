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
LANE_LIAISON_TAG = "lane:liaison"
TYPE_MONITOR_TAG = "type:monitor"
TYPE_CONTINUITY_TAG = "type:continuity"
_WATCH_ALIAS_PREFIX = "watch:"
_WATCHES_PREFIX = "watches:"

Spine = Literal["root", "work"]
LiaisonMonitorKind = Literal["monitor", "liaison_house", "other"]


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


def _rewrite_watch_alias(tags: list[str]) -> list[str]:
    """Store ``watch:<id>`` as ``watches:<id>``. Same key, one spelling."""
    rewritten: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if (
            tag.startswith(_WATCH_ALIAS_PREFIX)
            and tag.removeprefix(_WATCH_ALIAS_PREFIX).isdigit()
        ):
            tag = _WATCHES_PREFIX + tag.removeprefix(_WATCH_ALIAS_PREFIX)
        if tag in seen:
            continue
        seen.add(tag)
        rewritten.append(tag)
    return rewritten


def _has_watches(tags: list[str]) -> bool:
    return any(
        tag.startswith(_WATCHES_PREFIX) and tag.removeprefix(_WATCHES_PREFIX).isdigit()
        for tag in tags
    )


def liaison_monitor_kind(tags: list[str] | None) -> LiaisonMonitorKind:
    """Reader: monitor, liaison house, or neither.

    ``watches:<id>`` wins. Else ``lane:liaison`` together with ``role:root``.
    ``role:root`` alone is some other house. ``type:monitor`` alone is neither.
    """
    cleaned = _rewrite_watch_alias(normalize_tag_list(tags))
    if _has_watches(cleaned):
        return "monitor"
    if LANE_LIAISON_TAG in cleaned and ROLE_ROOT_TAG in cleaned:
        return "liaison_house"
    return "other"


def liaison_tag_on_child_without_root(tags: list[str] | None) -> bool:
    """True when a child lane carries ``lane:liaison`` and is not a house spine."""
    cleaned = normalize_tag_list(tags)
    return LANE_LIAISON_TAG in cleaned and ROLE_ROOT_TAG not in cleaned


def thread_is_child_lane(thread_id: str) -> bool:
    """True when the thread already has a lane parent."""
    from agent_bus_store.db.lane_associations import get_current_lane

    lane = get_current_lane(thread_id=thread_id)
    return lane.get("state") == "associated"


def gate_thread_tags(
    new_tags: list[str] | None,
    *,
    prior_tags: list[str] | None,
    enroll_charter_runner: bool = False,
    merge_prior: bool = False,
    child_lane: bool = False,
) -> list[str]:
    """Normalize + enrollment dual-key + role guard + enrolled⇒root stamp.

    ``merge_prior`` is the add-tags path (stored set is prior plus written).
    A replace path stores ``new_tags`` alone. Rows that already lack
    ``watches:`` keep accepting unrelated adds; a write that newly introduces
    ``type:monitor`` must include ``watches:<id>`` and must not also store
    ``type:continuity``.
    """
    cleaned = gate_enrollment_tags(
        new_tags,
        prior_tags=prior_tags,
        enroll_charter_runner=enroll_charter_runner,
    )
    unknown = sorted(
        tag for tag in cleaned if tag.startswith(_ROLE_PREFIX) and tag != ROLE_ROOT_TAG
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
    written = _rewrite_watch_alias(cleaned)
    prior = _rewrite_watch_alias(normalize_tag_list(prior_tags))
    stored = list(dict.fromkeys([*prior, *written])) if merge_prior else written
    prior_set = set(prior)
    touches_pair = TYPE_MONITOR_TAG in written or TYPE_CONTINUITY_TAG in written
    if touches_pair and TYPE_MONITOR_TAG in stored and TYPE_CONTINUITY_TAG in stored:
        raise ThreadClassificationError(
            detail=(
                "A monitor thread is type:monitor plus watches:<id>. "
                "Do not also stamp type:continuity."
            ),
            error_code="monitor_continuity_conflict",
        )
    adding_monitor = TYPE_MONITOR_TAG in written and TYPE_MONITOR_TAG not in prior_set
    if adding_monitor and not _has_watches(stored):
        raise ThreadClassificationError(
            detail=(
                "type:monitor requires a watches:<thread id> tag on the same write."
            ),
            error_code="monitor_watch_required",
        )
    adding_liaison = LANE_LIAISON_TAG in written and LANE_LIAISON_TAG not in prior_set
    if child_lane and adding_liaison and ROLE_ROOT_TAG not in stored:
        raise ThreadClassificationError(
            detail=(
                "lane:liaison belongs on a liaison house (with role:root), "
                "not on a child lane."
            ),
            error_code="liaison_tag_on_child_lane",
        )
    return written


def classification_denied_http(
    exc: BaseException,
) -> tuple[int, dict[str, Any]] | None:
    """Map ``ThreadClassificationError`` → (422, detail dict) for FastAPI routes."""
    if not isinstance(exc, ThreadClassificationError):
        return None
    return 422, {"error": exc.error_code, "detail": exc.detail}


__all__ = [
    "LANE_LIAISON_TAG",
    "ROLE_ROOT_TAG",
    "Spine",
    "LiaisonMonitorKind",
    "ThreadClassification",
    "ThreadClassificationError",
    "TYPE_MONITOR_TAG",
    "classification_denied_http",
    "classify_thread",
    "gate_thread_tags",
    "liaison_monitor_kind",
    "liaison_tag_on_child_without_root",
    "resolve_spine",
    "thread_is_child_lane",
]
