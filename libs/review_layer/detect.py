"""Pure review-target detection from resolved prompt bodies (no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ReviewTargetKind = Literal["lane_branch", "worktree_lane", "explicit_head"]

_LAYER_PRESENT_MARKER = "cortex://tmp/review-layers/"
_LANE_BRANCH = re.compile(r"\bcursor-sdk/lane-(\d+)\b")
_WORKTREE_LANE = re.compile(
    r"ulg-arc-worktrees/[^\s]*?/lane-(\d+)",
    re.IGNORECASE,
)
_REVIEW_HEAD = re.compile(r"^review_head:\s*(\S+)", re.MULTILINE)
_REVIEW_BASE = re.compile(r"^review_base:\s*(\S+)", re.MULTILINE)
_BARE_SHA256 = re.compile(r"\b[0-9a-f]{64}\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    """A single review head (and optional base) extracted from prompt text."""

    kind: ReviewTargetKind
    head: str
    base: str | None = None


@dataclass(frozen=True, slots=True)
class DetectionResult:
    layer_present: bool
    targets: tuple[ReviewTarget, ...]


def detect_review_targets(body: str) -> DetectionResult:
    """Extract review targets and layer-presence from a resolved prompt body."""
    if not body:
        return DetectionResult(layer_present=False, targets=())

    layer_present = _LAYER_PRESENT_MARKER in body
    targets: list[ReviewTarget] = []

    head_line = _REVIEW_HEAD.search(body)
    base_line = _REVIEW_BASE.search(body)
    if head_line:
        targets.append(
            ReviewTarget(
                kind="explicit_head",
                head=head_line.group(1).strip(),
                base=base_line.group(1).strip() if base_line else None,
            )
        )

    for match in _LANE_BRANCH.finditer(body):
        thread_id = match.group(1)
        head = f"cursor-sdk/lane-{thread_id}"
        if not any(t.head == head for t in targets):
            targets.append(ReviewTarget(kind="lane_branch", head=head))

    for match in _WORKTREE_LANE.finditer(body):
        thread_id = match.group(1)
        head = f"cursor-sdk/lane-{thread_id}"
        if not any(t.head == head for t in targets):
            targets.append(ReviewTarget(kind="worktree_lane", head=head))

    # Bare sha256 tokens in prose must not become implicit targets.
    if not targets and _BARE_SHA256.search(body):
        pass

    return DetectionResult(layer_present=layer_present, targets=tuple(targets))


def select_review_target(result: DetectionResult) -> ReviewTarget | None:
    """Choose the primary target for materialization (explicit head wins)."""
    if not result.targets:
        return None
    for target in result.targets:
        if target.kind == "explicit_head":
            return target
    return result.targets[0]
