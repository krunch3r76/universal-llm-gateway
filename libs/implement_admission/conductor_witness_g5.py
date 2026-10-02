"""G5 fold: attended SCORE_RESURFACE ∧ implement; away implement only (a:37198)."""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.conductor_witness_table import (
    _SHA_RE,
    _conductor_dispatch_id,
    _g3_journal_written_at,
    _repo_head_full_sha,
)
from implement_admission.conductor_witness_types import FoldDeps, Witness

_CDP_EXEC_RE = re.compile(
    r"(?:cdp\s+exec(?:ution)?(?:_id)?|execution_id)\s*[`:\s]+"
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
    re.IGNORECASE,
)
_REVIEW_SHA_RE = re.compile(
    r"(?:read_sha256|review\s+sha(?:256)?)\s*[`:\s]+([0-9a-f]{16,64})",
    re.IGNORECASE,
)


def tip_ratify_pins(tip_body: str) -> tuple[str | None, str | None]:
    """CDP exec id and review sha recorded on the tip, if present."""
    exec_match = _CDP_EXEC_RE.search(tip_body or "")
    sha_match = _REVIEW_SHA_RE.search(tip_body or "")
    return (
        exec_match.group(1) if exec_match else None,
        sha_match.group(1).lower() if sha_match else None,
    )


def normalize_resurface_read(raw: object) -> str:
    """Map bus reader output to found / not_found / unknown."""
    if raw is True:
        return "found"
    if raw is False:
        return "not_found"
    text = str(raw or "").strip().lower()
    if text in {"found", "not_found", "unknown"}:
        return text
    return "unknown"


def g5_implement_witness(
    *,
    tip_body: str,
    deps: FoldDeps,
    artifacts: dict[str, str],
    repo: Path | None,
) -> Witness | None:
    """Away-or-AND-half implement witness: nested ledger, else L1==HEAD."""
    if deps.nested_implement is not None:
        dispatch_id = _conductor_dispatch_id(tip_body)
        finder = getattr(deps.nested_implement, "parent_with_commits", None)
        hit: str | None = None
        if callable(finder):
            found = finder(tip_body=tip_body, explicit_parent_id=dispatch_id)
            hit = str(found) if found else None
        elif dispatch_id and deps.nested_implement.nested_implement_has_commits(
            nest_under_dispatch_id=dispatch_id,
        ):
            hit = dispatch_id
        if hit:
            return Witness(row="G5", source="ledger:nested_implement", detail=hit)
    l1_sha = artifacts.get("L1")
    if l1_sha and _SHA_RE.match(l1_sha) and repo is not None:
        head_sha = _repo_head_full_sha(repo)
        if head_sha is not None and head_sha == l1_sha.lower():
            return Witness(row="G5", source="git:lane_head", detail=l1_sha)
    return None


def hang_g5_witness(
    slug: str,
    *,
    tip_body: str,
    deps: FoldDeps,
    files_root: Path,
    artifacts: dict[str, str],
    repo: Path | None,
    g4_blocked: bool,
) -> Witness | None:
    """Hang G5 only when the mode's witnesses are complete."""
    if g4_blocked:
        return None
    implement = g5_implement_witness(
        tip_body=tip_body, deps=deps, artifacts=artifacts, repo=repo
    )
    summon = (deps.summon_mode or "").strip().lower().replace("-", "_")
    if summon != "attended":
        return implement
    if deps.bus is None or not deps.summoning_thread_id:
        return None
    after = _g3_journal_written_at(slug, files_root=files_root)
    exec_id, review_sha = tip_ratify_pins(tip_body)
    raw = deps.bus.has_score_resurface_after(
        thread_id=deps.summoning_thread_id,
        after_written_at=after,
        slug=slug,
        exec_id=exec_id,
        review_sha=review_sha,
    )
    if normalize_resurface_read(raw) != "found" or implement is None:
        return None
    return Witness(
        row="G5",
        source="bus:SCORE_RESURFACE",
        detail=f"{deps.summoning_thread_id};{implement.source}:{implement.detail}",
    )
