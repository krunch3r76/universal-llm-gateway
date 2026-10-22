"""G5 fold: attended SCORE_RESURFACE ∧ implement; away implement only (a:37198)."""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.conductor_witness_table import (
    _G3_ARTIFACT_IDS,
    _SHA_RE,
    _conductor_dispatch_id,
    _cortex_bytes_sha,
    _cortex_text,
    _first_resolving_artifact,
    _g3_journal_written_at,
    _repo_head_full_sha,
    _tip_row_uri,
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
            found = finder(
                tip_body=tip_body,
                explicit_parent_id=dispatch_id,
                work_key=deps.source_ref,
            )
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
        if head_sha is not None:
            l1 = l1_sha.lower()
            head = head_sha.lower()
            if head == l1:
                return Witness(row="G5", source="git:lane_head", detail=l1_sha)
            if deps.git is not None and deps.git.is_ancestor(l1, head):
                return Witness(row="G5", source="git:lane_head", detail=l1_sha)
    return None


_FILES_EXPECTED_H2_RE = re.compile(
    r"^##\s+files_expected\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_CORTEX_URI_RE = re.compile(r"cortex://[^\s`)]+")
_PRE_EDIT_READ_SHA_RE = re.compile(
    r"pre-edit read_sha256:\s*([0-9a-f]{64})\b",
    re.IGNORECASE,
)
_TIP_SHA256_RE = re.compile(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])")
_REPO_PATH_IN_ITEM_RE = re.compile(r"(?:^|\s)(?:libs/|services/)\S")
_WORKSPACES_URI_RE = re.compile(r"workspaces://[^\s`)]+")


def _files_expected_section(g3_text: str) -> str | None:
    match = _FILES_EXPECTED_H2_RE.search(g3_text)
    if not match:
        return None
    rest = g3_text[match.end() :]
    next_h2 = re.search(r"^##\s+", rest, re.MULTILINE)
    return rest[: next_h2.start()] if next_h2 else rest


def _parse_g3_files_expected_pairs(section: str) -> list[tuple[str, str]] | None:
    if _WORKSPACES_URI_RE.search(section):
        return None
    for match in re.finditer(r"`([^`]+)`", section):
        path = match.group(1).strip()
        if path.startswith(("libs/", "services/")):
            return None
        if path.lower() == "none":
            return None
    items = [chunk.strip() for chunk in re.split(r"(?m)^-\s+", section) if chunk.strip()]
    if not items:
        return None
    pairs: list[tuple[str, str]] = []
    for item in items:
        uris = _CORTEX_URI_RE.findall(item)
        if not uris:
            if _REPO_PATH_IN_ITEM_RE.search(item) or re.search(
                r"(?:^|\s)none(?:\s|$|`)", item, re.IGNORECASE
            ):
                return None
            continue
        if len(uris) != 1:
            return None
        uri = uris[0]
        remainder = _CORTEX_URI_RE.sub("", item, count=1)
        if (
            _WORKSPACES_URI_RE.search(remainder)
            or _REPO_PATH_IN_ITEM_RE.search(remainder)
            or re.search(r"(?:^|\s)none(?:\s|$|`)", remainder, re.IGNORECASE)
        ):
            return None
        pre_match = _PRE_EDIT_READ_SHA_RE.search(item)
        if not pre_match:
            return None
        pre_sha = pre_match.group(1).lower()
        pairs.append((uri, pre_sha))
    if len(pairs) < 1:
        return None
    return pairs


def _tip_sha256_set(tip_body: str) -> set[str]:
    return set(_TIP_SHA256_RE.findall((tip_body or "").lower()))


def select_g5_implement_witness(
    *,
    tip_body: str,
    deps: FoldDeps,
    files_root: Path,
    artifacts: dict[str, str],
    repo: Path | None,
) -> Witness | None:
    """Git/ledger implement first; else cortex-only files_expected transition."""
    git_or_ledger = g5_implement_witness(
        tip_body=tip_body, deps=deps, artifacts=artifacts, repo=repo
    )
    if git_or_ledger is not None:
        return git_or_ledger
    _, g3_uri = _first_resolving_artifact(
        artifacts, _G3_ARTIFACT_IDS, files_root=files_root, repo=repo
    )
    if g3_uri is None:
        fallback = _tip_row_uri(tip_body, "G3")
        if fallback and _cortex_bytes_sha(fallback, files_root=files_root) is not None:
            g3_uri = fallback
    if g3_uri is None:
        return None
    g3_text = _cortex_text(g3_uri, files_root=files_root)
    g3_spec_sha = _cortex_bytes_sha(g3_uri, files_root=files_root)
    if g3_text is None or g3_spec_sha is None:
        return None
    section = _files_expected_section(g3_text)
    if section is None:
        return None
    expected_pairs = _parse_g3_files_expected_pairs(section)
    if expected_pairs is None:
        return None
    tip_shas = _tip_sha256_set(tip_body)
    verified_pairs: list[tuple[str, str]] = []
    for uri, pre_sha in expected_pairs:
        actual_sha = _cortex_bytes_sha(uri, files_root=files_root)
        if actual_sha is None:
            return None
        actual_sha = actual_sha.lower()
        if (
            actual_sha == pre_sha
            or actual_sha == g3_spec_sha
            or actual_sha not in tip_shas
        ):
            return None
        verified_pairs.append((uri, actual_sha))
    return Witness(
        row="G5",
        source="cortex:files_expected",
        detail=",".join(f"{uri}={actual_sha}" for uri, actual_sha in verified_pairs),
    )


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
    implement = select_g5_implement_witness(
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        artifacts=artifacts,
        repo=repo,
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
