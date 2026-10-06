"""G-row witness table evaluation for conductor scoreboard fold."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from review_verdict.grammar import (
    _MERITS_RE,
    ParsedVerdict,
    VerdictAction,
    _parse_token_from_raw,
    parse_gate6_markdown,
)

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.conductor_score_journal import (
    G_ROWS,
    is_g_ladder_rows,
    load_journal,
)
from implement_admission.conductor_score_table import (
    SCOREBOARD_ROW_ID,
    cell,
    row_id_in,
    stops_index,
)
from implement_admission.conductor_witness_types import (
    FoldDeps,
    Witness,
    WitnessCortex,
    stops_block_reason,
)
from implement_admission.evidence_verify import resolve_artifact_path

_ARTIFACT_URI_RE = re.compile(
    r"^\|\s*(?P<id>[^|`\n]+?)\s*\|\s*(?:`(?P<cortex>cortex://[^`]+)`"
    r"|(?P<cortex_bare>cortex://[^\s|`]+)"
    r"|`?(?P<git>git:(?P<git_sha>[0-9a-f]{7,40}))`?"
    r"|`?(?P<sha>[0-9a-f]{7,40})`?\s+on\s+master)",
    re.MULTILINE | re.IGNORECASE,
)
_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_G4_BLOCKS_DONE_RE = re.compile(
    r"(?is)does not clear G5|withhold G5|withhold(?:s)? completeness"
    r"|AC-\d+\s*\|\s*\*\*FAIL\*\*"
)
_G4_VERDICT_WITHHOLD_RE = re.compile(
    r"(?m)^##\s*(?i:verdict)\s*$[^*]{0,400}?\*\*\s*(?:VERDICT:\s*)?(AMEND|REVISE|REJECT|BLOCK)\b"
)
_CITED_SHA_RE = re.compile(
    r"(?:`(?:sha256:)?([0-9a-f]{7,64})`|read_sha256[=:]([0-9a-f]{7,64}))",
    re.IGNORECASE,
)
_ROW_URI_RE = re.compile(
    rf"^\|\s*(?P<rid>{SCOREBOARD_ROW_ID})\s*\|.*?(?P<uri>cortex://[^\s|`]+)",
    re.MULTILINE | re.IGNORECASE,
)
_WITNESS_KIND_BIND = "BIND"
_WITNESS_KIND_LAND = "LAND"
_G2_ARTIFACT_IDS = ("F1", "S7")
_G3_ARTIFACT_IDS = ("S4b", "S9")
_G6_REVIEW_ARTIFACT_IDS = ("R1",)
_G6_STANDALONE_VERDICT_RE = re.compile(
    r"(?m)^VERDICT:\s*(?P<raw>.+?)\s*$",
    re.IGNORECASE,
)
# Skill cdp_fail_route vocabulary. U3 stamps hop_reason=cdp_probe_indeterminate
# (a different fact) and does not name which verdict governs.
_CDP_FAIL_ROUTE_RE = re.compile(
    r"(?<![A-Za-z0-9_])cdp_fail_route=(?P<route>nested-grok|operator)"
    r"(?![A-Za-z0-9_-])"
)


def _cdp_fail_route(tip_body: str, row_id: str) -> str | None:
    """Route named in one Gated row's Stops cell, if it is a known token."""
    column = stops_index(tip_body)
    for line in (tip_body or "").splitlines():
        if row_id_in(line) != row_id.upper():
            continue
        match = _CDP_FAIL_ROUTE_RE.search(cell(line, column))
        return match.group("route") if match else None
    return None


def _gated_review_keys(
    row_id: str, legacy: tuple[str, ...], route: str | None
) -> tuple[str, ...]:
    """Legacy ids, or only ``<row>-REVIEW-<route>`` once a route is stamped.

    A stamped route does not fall through to the legacy id. That id is the
    consult that failed, and treating it as the witness would let the
    commentary verdict govern.
    """
    if route is None:
        return legacy
    return (f"{row_id}-REVIEW-{route}",)


def _first_resolving_artifact(
    artifacts: dict[str, str],
    keys: tuple[str, ...],
    *,
    files_root: Path,
    repo: Path | None,
    route: str | None = None,
) -> tuple[str | None, str | None]:
    """First resolving artifact. A route keeps only ``*-REVIEW-<route>``.

    Other ids in ``keys`` are commentary for that fold. They do not resolve
    the row, including when they appear earlier in the tip.
    """
    chosen = keys
    if route:
        suffix = f"-REVIEW-{route}"
        chosen = tuple(key for key in keys if key.endswith(suffix))
    for key in chosen:
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
        cortex_uri = match.group("cortex") or match.group("cortex_bare")
        if cortex_uri:
            artifacts[artifact_id] = cortex_uri
        elif match.group("git_sha"):
            artifacts[artifact_id] = match.group("git_sha").lower()
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
        return True
    if repo is not None:
        found = resolve_artifact_path(uri, source_repo=repo, cortex_root=files_root)
        return found is not None and found.is_file()
    return False


def _g4_body_clears(uri: str, *, files_root: Path) -> bool:
    """URI-resolve is not enough: a withhold/FAIL G4 body is not a witness."""
    text = _cortex_text(uri, files_root=files_root)
    if text is None:
        return uri.startswith("cortex://") is False
    if _G4_BLOCKS_DONE_RE.search(text):
        return False
    return _G4_VERDICT_WITHHOLD_RE.search(text) is None


def _g6_review_body_witnesses(
    uri: str,
    *,
    files_root: Path,
    tip_body: str,
    artifact_id: str,
) -> bool:
    """After-ship review (R1): affirmative verdict + cited-sha bind."""
    return (
        _g6_review_failure_reason(
            uri,
            files_root=files_root,
            tip_body=tip_body,
            artifact_id=artifact_id,
        )
        is None
    )


def _g6_parsed_is_archive_bind(parsed: ParsedVerdict) -> bool:
    """Archive pre-land reviews stamp ``Verdict: BIND`` — G6-only, not shared grammar."""
    if parsed.token is None:
        return False
    parts = parsed.token.strip().split()
    if not parts:
        return False
    first = parts[0]
    if first.endswith("."):
        first = first[:-1]
    return first.upper() == "BIND"


def _g6_collect_standalone_verdict_lines(text: str) -> list[ParsedVerdict]:
    """Merits lines, gate-6 blocks, and whole-line ``VERDICT:`` — no prose-token fallback."""
    collected: list[ParsedVerdict] = []
    body = text or ""
    for line in body.splitlines():
        stripped = line.strip()
        match = _MERITS_RE.search(stripped)
        if match is None or match.start() != 0:
            continue
        parsed = _parse_token_from_raw(match.group(1))
        if parsed.token is not None:
            collected.append(parsed)
    gate6 = parse_gate6_markdown(body)
    if gate6.token is not None:
        collected.append(gate6)
    for match in _G6_STANDALONE_VERDICT_RE.finditer(body):
        parsed = _parse_token_from_raw(match.group("raw"))
        if parsed.token is not None:
            collected.append(parsed)
    return collected


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
    collected = _g6_collect_standalone_verdict_lines(text)
    if not collected:
        return "unrecognized review verdict"
    for parsed in collected:
        if parsed.action is VerdictAction.ADVANCE:
            continue
        if _g6_parsed_is_archive_bind(parsed):
            continue
        if parsed.reason == "unknown_verdict":
            return "unrecognized review verdict"
        return "negative review verdict"
    cited_sha = _artifact_cited_sha(tip_body, artifact_id)
    if cited_sha is None:
        return "missing_cited_sha"
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


def _entity_card(cortex: WitnessCortex, entity_id: str, **kwargs: Any) -> dict | None:
    """Return the entity, or None when cortex reports it missing."""
    from implement_admission.conductor_witness_defaults import WitnessEntityMissing

    try:
        doc = cortex.entity_get(entity_id, **kwargs)
    except WitnessEntityMissing:
        return None
    if not isinstance(doc, dict) or not doc:
        return None
    return doc


def _witness_g1(*, source_ref: str, cortex: WitnessCortex) -> Witness | None:
    from implement_admission.conductor_witness_defaults import WitnessEntityMissing

    todo_id = source_ref if source_ref.startswith("todo:") else source_ref
    try:
        relationships = cortex.list_relationships(todo_id, type_id="derived_from")
    except WitnessEntityMissing:
        return None
    for rel in relationships:
        target = str(rel.get("target_id") or "")
        if not target.startswith("document:"):
            continue
        doc = _entity_card(cortex, target, intent="card")
        if doc is None:
            continue
        attrs = doc.get("attributes") or {}
        kind = (
            str(attrs.get("consult_kind") or doc.get("consult_kind") or "")
            .strip()
            .lower()
        )
        if kind != "architecture":
            full = _entity_card(cortex, target, intent="full")
            if full is None:
                continue
            full_attrs = full.get("attributes") or {}
            kind = (
                str(full_attrs.get("consult_kind") or full.get("consult_kind") or kind)
                .strip()
                .lower()
            )
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


def _repo_head_full_sha(repo: Path) -> str | None:
    """Return full HEAD sha for a git working tree, or None if unavailable."""
    git_dir = repo / ".git"
    if not git_dir.exists():
        return None
    proc = subprocess.run(
        ["git", "-C", str(repo.resolve()), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    head = proc.stdout.strip().lower()
    if not _SHA_RE.match(head):
        return None
    return head


def _conductor_dispatch_id(tip_body: str) -> str | None:
    match = re.search(
        r"conductor dispatch_id[^:`]*[`\"]?([0-9a-f-]{7,}[0-9a-f](?:-r\d+)?)[`\"]?",
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
        witnesses["G2"] = Witness(row="G2", source=f"artifact:{g2_id}", detail=g2_uri)

    g3_id, g3_uri = _first_resolving_artifact(
        artifacts, _G3_ARTIFACT_IDS, files_root=files_root, repo=repo
    )
    if g3_id is None:
        g3_uri = _tip_row_uri(tip_body, "G3")
        if g3_uri and _uri_resolves(g3_uri, files_root=files_root, repo=repo):
            g3_id = "tip"
    if g3_id and g3_uri:
        witnesses["G3"] = Witness(row="G3", source=f"artifact:{g3_id}", detail=g3_uri)

    g4_route = _cdp_fail_route(tip_body, "G4")
    g4_id, g4_uri = _first_resolving_artifact(
        artifacts,
        _gated_review_keys("G4", ("G4",), g4_route),
        files_root=files_root,
        repo=repo,
        route=g4_route,
    )
    g4_stops = stops_block_reason(tip_body, "G4")
    if (
        g4_stops is None
        and g4_id
        and g4_uri
        and _g4_body_clears(g4_uri, files_root=files_root)
    ):
        g4_source = (
            f"witness:{_WITNESS_KIND_BIND}:{g4_id}:route={g4_route}"
            if g4_route
            else "artifact:G4"
        )
        witnesses["G4"] = Witness(row="G4", source=g4_source, detail=g4_uri)

    g4_blocked = g4_stops is not None or (bool(g4_uri) and witnesses["G4"] is None)
    from implement_admission.conductor_witness_g5 import hang_g5_witness

    witnesses["G5"] = hang_g5_witness(
        slug,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        artifacts=artifacts,
        repo=repo,
        g4_blocked=g4_blocked,
    )

    if witnesses.get("G5") is not None:
        g6_route = _cdp_fail_route(tip_body, "G6")
        g6_id, g6_uri = _first_resolving_artifact(
            artifacts,
            _gated_review_keys("G6", _G6_REVIEW_ARTIFACT_IDS, g6_route),
            files_root=files_root,
            repo=repo,
            route=g6_route,
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
            g6_source = (
                f"witness:{_WITNESS_KIND_BIND}:{g6_id}:route={g6_route}"
                if g6_route
                else f"artifact:{g6_id}"
            )
            witnesses["G6"] = Witness(row="G6", source=g6_source, detail=g6_uri)

    land_sha = artifacts.get(_land_artifact_key("G7"))
    if (
        land_sha
        and witnesses.get("G6") is not None
        and deps.git is not None
        and deps.git.is_ancestor(land_sha, "master")
    ):
        witnesses["G7"] = Witness(row="G7", source=f"git:{land_sha}", detail=land_sha)

    return witnesses
