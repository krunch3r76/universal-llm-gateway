"""Pure review-target detection from resolved prompt bodies (no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ReviewTargetKind = Literal["lane_branch", "worktree_lane", "explicit_head"]

_LAYER_URI = re.compile(
    r"cortex://tmp/review-layers/([A-Za-z0-9._-]+)/",
    re.IGNORECASE,
)
_LANE_BRANCH = re.compile(r"\bcursor-sdk/lane-(\d+)(?![\w-])")
_WORKTREE_LANE = re.compile(
    r"ulg-arc-worktrees/[^\s]*?/lane-(\d+)(?![\w-])",
    re.IGNORECASE,
)
_REVIEW_HEAD = re.compile(r"^review_head:\s*(\S+)", re.MULTILINE)
_REVIEW_BASE = re.compile(r"^review_base:\s*(\S+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    """A single review head (and optional base) extracted from prompt text."""

    kind: ReviewTargetKind
    head: str
    base: str | None = None


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Targets extracted from a prompt; layer presence is resolved separately on disk."""

    targets: tuple[ReviewTarget, ...]


def detect_review_targets(body: str) -> DetectionResult:
    """Extract review targets from explicit tokens in a resolved prompt body."""
    if not body:
        return DetectionResult(targets=())

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

    return DetectionResult(targets=tuple(targets))


def cited_layer_ids(body: str) -> tuple[str, ...]:
    """Return layer ids cited via ``cortex://tmp/review-layers/<id>/`` URIs."""
    return tuple(dict.fromkeys(_LAYER_URI.findall(body)))


def resolve_primary_target(result: DetectionResult) -> ReviewTarget | None:
    """Pick the materialization target: explicit ``review_head:`` wins, else one implicit."""
    explicit = [t for t in result.targets if t.kind == "explicit_head"]
    if explicit:
        return explicit[0]
    implicit_heads = sorted({t.head for t in result.targets if t.kind != "explicit_head"})
    if len(implicit_heads) == 1:
        head = implicit_heads[0]
        for target in result.targets:
            if target.head == head:
                return target
    return None


def implicit_target_heads(result: DetectionResult) -> tuple[str, ...]:
    """Distinct implicit heads (lane branch / worktree), excluding explicit ``review_head:``."""
    if any(t.kind == "explicit_head" for t in result.targets):
        return ()
    return tuple(sorted({t.head for t in result.targets}))
