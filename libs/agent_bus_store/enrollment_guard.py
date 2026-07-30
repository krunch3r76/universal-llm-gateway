"""Reserved enrollment-tag gate for agent-bus.

``charter-runner`` is not a free-form descriptive tag — it historically enrolled
a thread into the manage-hosted charter runner. **Control-plane retirement (R2):**
typed work-item admit on the ledger is the successor control path; dual-key enroll
is **not** required for admit→dispatch on the new path. This gate remains to block
accidental tag adds on review/consult threads.
"""

from __future__ import annotations

from typing import Any

# Must match charter_runner.eligibility.ENROLLMENT_TAG (lowercase after normalize).
ENROLLMENT_TAG = "charter-runner"


# Retired as control-plane requirement — typed ledger admit replaces (charter-runner-alt-arch).
ENROLL_CONTROL_RETIRED = True


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


def enroll_tag_required_for_control() -> bool:
    """Dual-key enroll is not load-bearing for typed admit → dispatch (R2 bind)."""
    return not ENROLL_CONTROL_RETIRED


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
    "ENROLL_CONTROL_RETIRED",
    "EnrollmentTagError",
    "enroll_tag_required_for_control",
    "enrollment_denied_http",
    "gate_enrollment_tags",
    "normalize_tag_list",
]
