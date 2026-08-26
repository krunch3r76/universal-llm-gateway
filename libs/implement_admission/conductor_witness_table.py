"""G-row witness table evaluation for conductor scoreboard fold."""

from __future__ import annotations

import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import G_ROWS, load_journal
from implement_admission.conductor_witness_types import (
    FoldDeps,
    Witness,
    WitnessCortex,
)
from implement_admission.evidence_verify import resolve_artifact_path

_ARTIFACT_URI_RE = re.compile(
    r"^\|\s*(?P<id>[^|`\n]+?)\s*\|\s*(?:`(?P<cortex>cortex://[^`]+)`"
    r"|`?(?P<sha>[0-9a-f]{7,40})`?\s+on\s+master)",
    re.MULTILINE | re.IGNORECASE,
)
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_G4_BLOCKS_DONE_RE = re.compile(
    r"(?is)does not clear G5|withhold G5|withhold(?:s)? completeness"
    r"|AC-\d+\s*\|\s*\*\*FAIL\*\*"
)


def _artifact_map(tip_body: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for match in _ARTIFACT_URI_RE.finditer(tip_body):
        artifact_id = match.group("id").strip()
        if match.group("cortex"):
            artifacts[artifact_id] = match.group("cortex")
        elif match.group("sha"):
            artifacts[artifact_id] = match.group("sha").lower()
    return artifacts


def _uri_resolves(uri: str, *, files_root: Path, repo: Path | None) -> bool:
    if uri.startswith("cortex://"):
        rel = uri.removeprefix("cortex://")
        return (files_root / rel).is_file()
    if _SHA_RE.fullmatch(uri):
        return True
    if repo is not None:
        found = resolve_artifact_path(uri, source_repo=repo, cortex_root=files_root)
        return found is not None and found.is_file()
    return False


def _g4_body_clears(uri: str, *, files_root: Path) -> bool:
    """URI-resolve is not enough: a withhold/FAIL G4 body is not a witness."""
    if not uri.startswith("cortex://"):
        return True
    path = files_root / uri.removeprefix("cortex://")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return _G4_BLOCKS_DONE_RE.search(text) is None


def _g3_journal_written_at(slug: str, *, files_root: Path) -> str | None:
    for record in reversed(load_journal(slug, files_root=files_root)):
        rows = record.get("rows") or []
        if "G3" in rows:
            written = record.get("written_at")
            return str(written) if written else None
    return None


def _witness_g1(*, source_ref: str, cortex: WitnessCortex) -> Witness | None:
    todo_id = source_ref if source_ref.startswith("todo:") else source_ref
    for rel in cortex.list_relationships(todo_id, type_id="derived_from"):
        target = str(rel.get("target_id") or "")
        if not target.startswith("document:"):
            continue
        doc = cortex.entity_get(target, intent="card")
        attrs = doc.get("attributes") or {}
        kind = str(attrs.get("consult_kind") or doc.get("consult_kind") or "").strip().lower()
        if kind != "architecture":
            summary = str(doc.get("summary_row") or "")
            if "consult_kind=architecture" in summary.lower().replace(" ", ""):
                kind = "architecture"
        if kind == "architecture":
            rel_id = rel.get("id")
            return Witness(
                row="G1",
                source=f"derived_from:{rel_id}",
                detail=target,
            )
    return None


def _conductor_dispatch_id(tip_body: str) -> str | None:
    match = re.search(
        r"conductor dispatch_id[^:`]*[`\"]?([0-9a-f-]{8,})[`\"]?",
        tip_body,
        re.IGNORECASE,
    )
    return match.group(1) if match else None


def row_witnesses(
    slug: str,
    *,
    tip_body: str,
    deps: FoldDeps,
    files_root: Path | None = None,
) -> dict[str, Witness | None]:
    """Evaluate v1 witness table for each G-row against graph/bus/git readers."""
    root = files_root if files_root is not None else cortex_files_root()
    repo = deps.repo
    source_ref = deps.source_ref or f"todo:{slug}"
    artifacts = _artifact_map(tip_body)
    witnesses: dict[str, Witness | None] = {gid: None for gid in G_ROWS}

    witnesses["G1"] = _witness_g1(source_ref=source_ref, cortex=deps.cortex)

    f_uri = artifacts.get("F1")
    if f_uri and _uri_resolves(f_uri, files_root=root, repo=repo):
        witnesses["G2"] = Witness(row="G2", source="artifact:F1", detail=f_uri)

    s4b_uri = artifacts.get("S4b")
    if s4b_uri and _uri_resolves(s4b_uri, files_root=root, repo=repo):
        entity = deps.cortex.entity_get(source_ref, intent="card")
        triage = str((entity.get("attributes") or {}).get("density_triage") or "")
        if triage.strip().lower() != "implement_ready":
            witnesses["G3"] = Witness(row="G3", source="artifact:S4b", detail=s4b_uri)

    g4_uri = artifacts.get("G4")
    if (
        g4_uri
        and _uri_resolves(g4_uri, files_root=root, repo=repo)
        and _g4_body_clears(g4_uri, files_root=root)
    ):
        witnesses["G4"] = Witness(row="G4", source="artifact:G4", detail=g4_uri)

    g4_blocked = bool(g4_uri) and witnesses["G4"] is None
    summon = (deps.summon_mode or "").strip().lower().replace("-", "_")
    if g4_blocked:
        witnesses["G5"] = None
    elif summon == "attended" and deps.bus is not None and deps.summoning_thread_id:
        after = _g3_journal_written_at(slug, files_root=root)
        if deps.bus.has_score_resurface_after(
            thread_id=deps.summoning_thread_id,
            after_written_at=after,
        ):
            witnesses["G5"] = Witness(
                row="G5",
                source="bus:SCORE_RESURFACE",
                detail=deps.summoning_thread_id,
            )
    elif deps.bus is not None:
        dispatch_id = _conductor_dispatch_id(tip_body)
        if dispatch_id and deps.bus.nested_implement_has_commits(
            nest_under_dispatch_id=dispatch_id,
        ):
            witnesses["G5"] = Witness(
                row="G5",
                source="ledger:nested_implement",
                detail=dispatch_id,
            )

    land_sha = artifacts.get("L1")
    if land_sha and deps.git is not None and deps.git.is_ancestor(land_sha, "master"):
        witnesses["G6"] = Witness(row="G6", source=f"git:{land_sha}", detail=land_sha)

    return witnesses
