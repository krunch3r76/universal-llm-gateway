"""G-row witness table evaluation for conductor scoreboard fold."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import (
    G_ROWS,
    _SCOREBOARD_ROW_ID,
    is_g_ladder_rows,
    load_journal,
)
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
_G6_REVIEW_AFFIRMATIVE_RE = re.compile(
    r"(?im)^\s*VERDICT:\s*(RATIFY(?:_WITH_CONDITIONS|-WITH-CONDITIONS)?)\s*$"
)
_G6_REVIEW_NEGATIVE_RE = re.compile(
    r"(?im)^\s*VERDICT:\s*(REVISE|REJECT|SCOPE-DRIFT|SCOPE_DRIFT)\s*$"
)
_CITED_SHA_RE = re.compile(
    r"(?:`(?:sha256:)?([0-9a-f]{7,64})`|read_sha256[=:]([0-9a-f]{7,64}))",
    re.IGNORECASE,
)
_ROW_URI_RE = re.compile(
    rf"^\|\s*(?P<rid>{_SCOREBOARD_ROW_ID})\s*\|.*?(?P<uri>cortex://[^\s|`]+)",
    re.MULTILINE | re.IGNORECASE,
)
_WITNESS_KIND_BIND = "BIND"
_WITNESS_KIND_LAND = "LAND"
_G2_ARTIFACT_IDS = ("F1", "S7")
_G3_ARTIFACT_IDS = ("S4b", "S9")
_G6_REVIEW_ARTIFACT_IDS = ("R1",)


def _first_resolving_artifact(
    artifacts: dict[str, str],
    keys: tuple[str, ...],
    *,
    files_root: Path,
    repo: Path | None,
) -> tuple[str | None, str | None]:
    for key in keys:
        uri = artifacts.get(key)
        if uri and _uri_resolves(uri, files_root=files_root, repo=repo):
            return key, uri
    return None, None


def _tip_row_uri(tip_body: str, row_id: str) -> str | None:
    for match in _ROW_URI_RE.finditer(tip_body):
        if match.group("rid").upper() == row_id.upper():
            return match.group("uri")
    return None


def _bind_artifact_keys(row_id: str) -> tuple[str, ...]:
    """Sidecar artifact ids that witness BIND for one scoreboard row."""
    if row_id == "G2":
        return _G2_ARTIFACT_IDS
    if row_id == "G3":
        return _G3_ARTIFACT_IDS
    return (f"{row_id}-{_WITNESS_KIND_BIND}",)


def _land_artifact_key(row_id: str) -> str:
    if row_id == "G7":
        return "L1"
    return f"{row_id}-{_WITNESS_KIND_LAND}"


def _artifact_map(tip_body: str) -> dict[str, str]:
    artifacts: dict[str, str] = {}
    for match in _ARTIFACT_URI_RE.finditer(tip_body):
        artifact_id = match.group("id").strip()
        if match.group("cortex"):
            artifacts[artifact_id] = match.group("cortex")
        elif match.group("sha"):
            artifacts[artifact_id] = match.group("sha").lower()
    return artifacts


def _artifact_cited_sha(tip_body: str, artifact_id: str) -> str | None:
    """Return a cited sha256 digest for one sidecar artifact row, if present."""
    row_re = re.compile(
        rf"^\|\s*{re.escape(artifact_id)}\s*\|[^\n]*$",
        re.MULTILINE | re.IGNORECASE,
    )
    match = row_re.search(tip_body)
    if not match:
        return None
    for sha_match in _CITED_SHA_RE.finditer(match.group(0)):
        digest = sha_match.group(1) or sha_match.group(2)
        if digest:
            return digest.lower()
    return None


def _cortex_text(uri: str, *, files_root: Path) -> str | None:
    if not uri.startswith("cortex://"):
        return None
    path = files_root / uri.removeprefix("cortex://")
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _cortex_bytes_sha(uri: str, *, files_root: Path) -> str | None:
    if not uri.startswith("cortex://"):
        return None
    path = files_root / uri.removeprefix("cortex://")
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _sha_digest_matches(cited: str, actual: str) -> bool:
    cited_norm = cited.lower().removeprefix("sha256:")
    actual_norm = actual.lower().removeprefix("sha256:")
    if len(cited_norm) < len(actual_norm):
        return actual_norm.startswith(cited_norm)
    return cited_norm == actual_norm


def _uri_resolves(uri: str, *, files_root: Path, repo: Path | None) -> bool:
    if uri.startswith("cortex://"):
        rel = uri.removeprefix("cortex://")
        return (files_root / rel).is_file()
    if _SHA_RE.fullmatch(uri):
        return False
    if repo is not None:
        found = resolve_artifact_path(uri, source_repo=repo, cortex_root=files_root)
        return found is not None and found.is_file()
    return False


def _g4_body_clears(uri: str, *, files_root: Path) -> bool:
    """URI-resolve is not enough: a withhold/FAIL G4 body is not a witness."""
    text = _cortex_text(uri, files_root=files_root)
    if text is None:
        return uri.startswith("cortex://") is False
    return _G4_BLOCKS_DONE_RE.search(text) is None


def _g6_review_body_witnesses(
    uri: str,
    *,
    files_root: Path,
    tip_body: str,
    artifact_id: str,
) -> bool:
    """After-ship review (R1): affirmative verdict + optional cited-sha bind."""
    return _g6_review_failure_reason(
        uri,
        files_root=files_root,
        tip_body=tip_body,
        artifact_id=artifact_id,
    ) is None


def _g6_review_failure_reason(
    uri: str,
    *,
    files_root: Path,
    tip_body: str,
    artifact_id: str,
) -> str | None:
    """Return a block reason when an R1 artifact does not witness G6."""
    text = _cortex_text(uri, files_root=files_root)
    if text is None:
        return "artifact unreadable"
    if _G6_REVIEW_NEGATIVE_RE.search(text):
        return "negative review verdict"
    if _G6_REVIEW_AFFIRMATIVE_RE.search(text) is None:
        return "unrecognized review verdict"
    cited_sha = _artifact_cited_sha(tip_body, artifact_id)
    if cited_sha is None:
        return None
    actual_sha = _cortex_bytes_sha(uri, files_root=files_root)
    if actual_sha is None:
        return "artifact unreadable"
    if not _sha_digest_matches(cited_sha, actual_sha):
        return "witness_sha_mismatch"
    return None


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
            blob = " ".join(
                (
                    str(doc.get("summary_row") or ""),
                    str(doc.get("description") or ""),
                    str(attrs.get("description") or ""),
                )
            )
            if "consult_kind=architecture" in blob.lower().replace(" ", ""):
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
    rows: tuple[str, ...] = G_ROWS,
) -> dict[str, Witness | None]:
    """Evaluate witness table for each scoreboard row against graph/bus/git readers."""
    root = files_root if files_root is not None else cortex_files_root()
    repo = deps.repo
    source_ref = deps.source_ref or f"todo:{slug}"
    artifacts = _artifact_map(tip_body)
    witnesses: dict[str, Witness | None] = {row_id: None for row_id in rows}

    if is_g_ladder_rows(rows):
        return _row_witnesses_g_ladder(
            slug,
            tip_body=tip_body,
            deps=deps,
            files_root=root,
            repo=repo,
            source_ref=source_ref,
            artifacts=artifacts,
            witnesses=witnesses,
        )
    return _row_witnesses_custom(
        tip_body=tip_body,
        deps=deps,
        files_root=root,
        repo=repo,
        artifacts=artifacts,
        rows=rows,
        witnesses=witnesses,
    )


def _row_witnesses_custom(
    *,
    tip_body: str,
    deps: FoldDeps,
    files_root: Path,
    repo: Path | None,
    artifacts: dict[str, str],
    rows: tuple[str, ...],
    witnesses: dict[str, Witness | None],
) -> dict[str, Witness | None]:
    for row_id in rows:
        bind_id, bind_uri = _first_resolving_artifact(
            artifacts,
            _bind_artifact_keys(row_id),
            files_root=files_root,
            repo=repo,
        )
        if bind_id is None:
            bind_uri = _tip_row_uri(tip_body, row_id)
            if bind_uri and _uri_resolves(bind_uri, files_root=files_root, repo=repo):
                bind_id = "tip"
        if bind_id and bind_uri:
            witnesses[row_id] = Witness(
                row=row_id,
                source=f"witness:{_WITNESS_KIND_BIND}:{bind_id}",
                detail=bind_uri,
            )
            continue
        land_uri = artifacts.get(_land_artifact_key(row_id))
        if (
            land_uri
            and deps.git is not None
            and _SHA_RE.fullmatch(land_uri)
            and deps.git.is_ancestor(land_uri, "master")
        ):
            witnesses[row_id] = Witness(
                row=row_id,
                source=f"witness:{_WITNESS_KIND_LAND}",
                detail=land_uri,
            )
    return witnesses


def _row_witnesses_g_ladder(
    slug: str,
    *,
    tip_body: str,
    deps: FoldDeps,
    files_root: Path,
    repo: Path | None,
    source_ref: str,
    artifacts: dict[str, str],
    witnesses: dict[str, Witness | None],
) -> dict[str, Witness | None]:
    witnesses["G1"] = _witness_g1(source_ref=source_ref, cortex=deps.cortex)

    g2_id, g2_uri = _first_resolving_artifact(
        artifacts, _G2_ARTIFACT_IDS, files_root=files_root, repo=repo
    )
    if g2_id is None:
        g2_uri = _tip_row_uri(tip_body, "G2")
        if g2_uri and _uri_resolves(g2_uri, files_root=files_root, repo=repo):
            g2_id = "tip"
    if g2_id and g2_uri:
        witnesses["G2"] = Witness(
            row="G2", source=f"artifact:{g2_id}", detail=g2_uri
        )

    g3_id, g3_uri = _first_resolving_artifact(
        artifacts, _G3_ARTIFACT_IDS, files_root=files_root, repo=repo
    )
    if g3_id is None:
        g3_uri = _tip_row_uri(tip_body, "G3")
        if g3_uri and _uri_resolves(g3_uri, files_root=files_root, repo=repo):
            g3_id = "tip"
    if g3_id and g3_uri:
        entity = deps.cortex.entity_get(source_ref, intent="card")
        triage = str((entity.get("attributes") or {}).get("density_triage") or "")
        if triage.strip().lower() != "implement_ready":
            witnesses["G3"] = Witness(
                row="G3", source=f"artifact:{g3_id}", detail=g3_uri
            )

    g4_uri = artifacts.get("G4")
    if (
        g4_uri
        and _uri_resolves(g4_uri, files_root=files_root, repo=repo)
        and _g4_body_clears(g4_uri, files_root=files_root)
    ):
        witnesses["G4"] = Witness(row="G4", source="artifact:G4", detail=g4_uri)

    g4_blocked = bool(g4_uri) and witnesses["G4"] is None
    summon = (deps.summon_mode or "").strip().lower().replace("-", "_")
    if g4_blocked:
        witnesses["G5"] = None
    elif summon == "attended" and deps.bus is not None and deps.summoning_thread_id:
        after = _g3_journal_written_at(slug, files_root=files_root)
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

    if witnesses.get("G5") is not None:
        g6_id, g6_uri = _first_resolving_artifact(
            artifacts, _G6_REVIEW_ARTIFACT_IDS, files_root=files_root, repo=repo
        )
        if (
            g6_id
            and g6_uri
            and _g6_review_body_witnesses(
                g6_uri,
                files_root=files_root,
                tip_body=tip_body,
                artifact_id=g6_id,
            )
        ):
            witnesses["G6"] = Witness(
                row="G6", source=f"artifact:{g6_id}", detail=g6_uri
            )

    land_sha = artifacts.get(_land_artifact_key("G7"))
    if (
        land_sha
        and witnesses.get("G6") is not None
        and deps.git is not None
        and deps.git.is_ancestor(land_sha, "master")
    ):
        witnesses["G7"] = Witness(row="G7", source=f"git:{land_sha}", detail=land_sha)

    return witnesses
