"""Reserved enrollment-tag gate for agent-bus.

``charter-runner`` is not a free-form descriptive tag — it enrolls a thread into
the manage-hosted charter runner. Accidental inclusion (e.g. a review
thread tagged ``charter-runner`` alongside ``type:review``) causes the charter runner to
scan a non-charter enrollment (``no_checkpoint`` noise / wasted capacity).

Structural rule: **adding** the enrollment tag requires an explicit
``enroll_charter_runner=true`` on the write. Keeping an already-enrolled tag or
removing it never requires the flag.
"""

from __future__ import annotations

from typing import Any

# Must match charter_runner.eligibility.ENROLLMENT_TAG (lowercase after normalize).
ENROLLMENT_TAG = "charter-runner"


class EnrollmentTagError(ValueError):
    """Raised when a write would newly add the enrollment tag without consent."""

    def __init__(self, *, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
        self.error_code = "reserved_enrollment_tag"


def normalize_tag_list(tags: list[str] | None) -> list[str]:
    """Strip + lowercase + dedupe (order-preserving) — shared with DB normalize."""
    if not tags:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        tag = str(raw).strip().lower()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        cleaned.append(tag)
    return cleaned


def gate_enrollment_tags(
    new_tags: list[str] | None,
    *,
    prior_tags: list[str] | None,
    enroll_charter_runner: bool = False,
) -> list[str]:
    """Return normalized tags, or raise ``EnrollmentTagError`` on illegal add.

    - ``enroll_charter_runner=True`` → enrollment tag may be newly present.
    - Tag already on ``prior_tags`` → keep allowed without the flag (idempotent).
    - Tag absent from prior and present in new without flag → denied.
    - Removing the tag is always allowed.
    """
    cleaned = normalize_tag_list(new_tags)
    prior = set(normalize_tag_list(prior_tags))
    adding = ENROLLMENT_TAG in cleaned and ENROLLMENT_TAG not in prior
    if adding and not enroll_charter_runner:
        raise EnrollmentTagError(
            detail=(
                f"Tag {ENROLLMENT_TAG!r} is reserved for charter-runner enrollment. "
                "Pass enroll_charter_runner=true to opt in, or omit the tag. "
                "Free-form tags (type:/project:/…) must not include it."
            )
        )
    return cleaned


def enrollment_denied_http(exc: BaseException) -> tuple[int, dict[str, Any]] | None:
    """Map tag-gate errors → (422, detail dict) for FastAPI routes.

    Handles ``EnrollmentTagError`` and ``ThreadClassificationError`` (spine
    role guard) so write routes can share one mapper.
    """
    if isinstance(exc, EnrollmentTagError):
        return 422, {"error": exc.error_code, "detail": exc.detail}
    from agent_bus_store.thread_classification import ThreadClassificationError

    if isinstance(exc, ThreadClassificationError):
        return 422, {"error": exc.error_code, "detail": exc.detail}
    return None


__all__ = [
    "ENROLLMENT_TAG",
    "EnrollmentTagError",
    "enrollment_denied_http",
    "gate_enrollment_tags",
    "normalize_tag_list",
]
