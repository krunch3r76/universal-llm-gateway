"""Pure review-target detection from resolved prompt bodies (no I/O)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

ReviewTargetKind = Literal[
    "review_thread",
    "lane_branch",
    "worktree_lane",
    "explicit_head",
]

_LANE_BRANCH = re.compile(r"\bcursor-sdk/lane-(\d+)(?![\w-])")
_WORKTREE_LANE = re.compile(
    r"ulg-arc-worktrees/[^\s]*?/lane-(\d+)(?![\w-])",
    re.IGNORECASE,
)
_REVIEW_THREAD = re.compile(r"^review_thread:[ \t]*(\d+)\s*$", re.MULTILINE)
_REVIEW_HEAD = re.compile(r"^review_head:[ \t]*(\S+)", re.MULTILINE)
_REVIEW_BASE = re.compile(r"^review_base:[ \t]*(\S+)", re.MULTILINE)


@dataclass(frozen=True, slots=True)
class ReviewTarget:
    """A review lane thread and optional explicit head/base refs."""

    kind: ReviewTargetKind
    thread_id: str
    head: str | None = None
    base: str | None = None


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Targets extracted from a prompt body."""

    targets: tuple[ReviewTarget, ...]
    explicit_head: str | None = None
    explicit_base: str | None = None


def distinct_explicit_review_threads(body: str) -> tuple[str, ...]:
    """Distinct thread ids from line-start ``review_thread:`` tokens."""
    if not body:
        return ()
    return tuple(sorted({m.group(1) for m in _REVIEW_THREAD.finditer(body)}))


def detect_review_targets(body: str) -> DetectionResult:
    """Extract review targets from explicit tokens in a resolved prompt body."""
    if not body:
        return DetectionResult(targets=())

    explicit_head = None
    explicit_base = None
    head_line = _REVIEW_HEAD.search(body)
    base_line = _REVIEW_BASE.search(body)
    if head_line:
        explicit_head = head_line.group(1).strip()
    if base_line:
        explicit_base = base_line.group(1).strip()

    targets: list[ReviewTarget] = []

    thread_line = _REVIEW_THREAD.search(body)
    if thread_line:
        tid = thread_line.group(1)
        targets.append(
            ReviewTarget(
                kind="review_thread",
                thread_id=tid,
                head=explicit_head,
                base=explicit_base,
            )
        )
        return DetectionResult(
            targets=tuple(targets),
            explicit_head=explicit_head,
            explicit_base=explicit_base,
        )

    seen_threads: set[str] = set()
    for match in _LANE_BRANCH.finditer(body):
        thread_id = match.group(1)
        if thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        targets.append(
            ReviewTarget(
                kind="lane_branch",
                thread_id=thread_id,
                head=explicit_head,
                base=explicit_base,
            )
        )

    for match in _WORKTREE_LANE.finditer(body):
        thread_id = match.group(1)
        if thread_id in seen_threads:
            continue
        seen_threads.add(thread_id)
        targets.append(
            ReviewTarget(
                kind="worktree_lane",
                thread_id=thread_id,
                head=explicit_head,
                base=explicit_base,
            )
        )

    return DetectionResult(
        targets=tuple(targets),
        explicit_head=explicit_head,
        explicit_base=explicit_base,
    )


def implicit_lane_thread_ids(result: DetectionResult) -> tuple[str, ...]:
    """Distinct lane thread ids when ``review_thread:`` is absent."""
    if any(t.kind == "review_thread" for t in result.targets):
        return ()
    return tuple(sorted({t.thread_id for t in result.targets}))


def resolve_primary_target(result: DetectionResult) -> ReviewTarget | None:
    """Choose the single lane target; ``review_thread:`` wins over implicit tokens."""
    explicit = [t for t in result.targets if t.kind == "review_thread"]
    if explicit:
        return explicit[0]
    threads = implicit_lane_thread_ids(result)
    if len(threads) == 1:
        tid = threads[0]
        for target in result.targets:
            if target.thread_id == tid:
                return target
    return None
