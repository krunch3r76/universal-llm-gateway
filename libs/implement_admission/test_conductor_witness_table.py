"""P2 affirmative sha-bound witness fold — AC3–AC6 (a:32391)."""

from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_score_journal import G_ROWS
from implement_admission.conductor_witness import (
    FoldDeps,
    fold_scoreboard,
    row_witnesses,
)
from implement_admission.conductor_witness_table import (
    _G2_ARTIFACT_IDS,
    _G3_ARTIFACT_IDS,
    _artifact_map,
    _first_resolving_artifact,
    _g4_body_clears,
    _g6_review_failure_reason,
    _uri_resolves,
)
from implement_admission.degraded_reasons import (
    stops_block_reason,
)

pytestmark = pytest.mark.offline

_SLUG = "conductor-hop-wait-protocol-fixture"
_SOURCE_REF = "todo:conductor-hop-wait-protocol"


class _StubCortex:
    def __init__(self, *, triage: str = "judgment_required") -> None:
        self._triage = triage

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {"id": entity_id, "attributes": {"density_triage": self._triage}}

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = entity_id, type_id
        return []


class _StubBus:
    def has_score_resurface_after(
        self,
        *,
        thread_id: str,
        after_written_at: str | None,
    ) -> bool:
        _ = thread_id, after_written_at
        return True


class _StubNestedImplement:
    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        _ = nest_under_dispatch_id
        return False


class _StubGit:
    def is_ancestor(self, commit: str, ref: str) -> bool:
        _ = commit, ref
        return False


def _review_tip(*, cited_sha: str | None = None, body: str) -> str:
    reviews = "cortex://notes/system/reviews/conductor-hop-wait-protocol-g6-verdict.md"
    sha_cell = f" `sha256:{cited_sha}`" if cited_sha else ""
    return (
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n"
        "|---|---|---|\n"
        f"| R1 | `{reviews}`{sha_cell} | G7 after-ship verdict |\n"
        f"\n<!-- body written to {reviews} -->\n"
    )


def _write_review(files_root: Path, body: str) -> str:
    path = files_root / "notes/system/reviews/conductor-hop-wait-protocol-g6-verdict.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return "cortex://notes/system/reviews/conductor-hop-wait-protocol-g6-verdict.md"


def _deps(tmp_path: Path, *, triage: str = "judgment_required") -> FoldDeps:
    return FoldDeps(
        cortex=_StubCortex(triage=triage),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="10110",
        repo=tmp_path / "repo",
    )


def _g5_precondition_tip(
    files_root: Path,
    review_body: str,
    *,
    cited_sha: str | None = None,
) -> str:
    _write_review(files_root, review_body)
    return _review_tip(cited_sha=cited_sha, body=review_body)


@pytest.mark.parametrize("verdict_line", ["VERDICT: REVISE", "VERDICT: REVISE\n"])
def test_ac3_revise_body_does_not_witness_g6(tmp_path: Path, verdict_line: str) -> None:
    """AC3 — live REVISE body ⇒ G6 ≠ DONE."""
    files_root = tmp_path / "cortex"
    review_body = f"{verdict_line}\nScope notes.\n"
    tip_body = _g5_precondition_tip(files_root, review_body)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G5") is not None
    assert witnesses.get("G6") is None


def test_ac4_ratify_with_conditions_not_witness_g6(tmp_path: Path) -> None:
    """AC4 — grammar AMENDMENTS_REQUIRED (RATIFY_WITH_CONDITIONS) ⇒ no G6 witness."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: RATIFY_WITH_CONDITIONS\n\nMinor nits only.\n"
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None


def test_ac5_cited_sha_mismatch_not_witness(tmp_path: Path) -> None:
    """AC5 — cited sha ≠ bytes ⇒ witness_sha_mismatch."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: RATIFY\n"
    _write_review(files_root, review_body)
    wrong_sha = "42a0bae98abb0000000000000000000000000000000000000000000000000000"
    tip_body = _review_tip(cited_sha=wrong_sha, body=review_body)
    deps = _deps(tmp_path)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None
    fold = fold_scoreboard(
        _SLUG,
        deps=deps,
        files_root=files_root,
        write_journal=False,
    )
    assert fold is None  # no scoreboard file seeded


def test_ac5_fold_reports_witness_sha_mismatch(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    review_body = "VERDICT: RATIFY\n"
    _write_review(files_root, review_body)
    wrong_sha = "42a0bae98abb0000000000000000000000000000000000000000000000000000"
    tip_body = (
        "# Scoreboard\n\n## Gated deliverables\n\n| ID | Status |\n|---|---|\n"
        + _review_tip(cited_sha=wrong_sha, body=review_body)
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip_body, encoding="utf-8")
    deps = _deps(tmp_path)
    fold = fold_scoreboard(
        _SLUG,
        deps=deps,
        files_root=files_root,
        write_journal=False,
    )
    assert fold is not None
    assert fold.witnesses.get("G6") is None
    assert fold.missing_witnesses.get("G6") == "witness_sha_mismatch"


def test_r2_c6_affirmative_without_cited_sha_not_witness(tmp_path: Path) -> None:
    """R2 C6 — affirmative verdict without cited sha ⇒ G6 unbound (fail-closed)."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: RATIFY\n"
    tip_body = _g5_precondition_tip(files_root, review_body)
    deps = _deps(tmp_path)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G5") is not None
    assert witnesses.get("G6") is None

    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    score_tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n| ID | Status |\n|---|---|\n"
        + tip_body
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(score_tip, encoding="utf-8")
    fold = fold_scoreboard(
        _SLUG,
        deps=deps,
        files_root=files_root,
        write_journal=False,
    )
    assert fold is not None
    assert fold.witnesses.get("G6") is None
    assert fold.missing_witnesses.get("G6") == "missing_cited_sha"


def test_ac6_unrecognized_verdict_not_witness(tmp_path: Path) -> None:
    """AC6 — unrecognized verdict vocabulary ⇒ fail-closed."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: MAYBE_OK\nLooks fine I guess.\n"
    tip_body = _g5_precondition_tip(files_root, review_body)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None


_LANDED_HARVEST_R1_BODY = """# Pre-land review — cdp-opus harvest shape

Merits: RATIFY

Ship when G6 row binds; action ADVANCE is the harvest disposition elsewhere.

## Verdict

**Verdict:** **RATIFY**
"""


def test_g6_harvest_merits_ratify_witnesses_with_cited_sha(tmp_path: Path) -> None:
    """Landed harvest (Merits + gate-6 RATIFY) witnesses when sha cited."""
    files_root = tmp_path / "cortex"
    review_body = _LANDED_HARVEST_R1_BODY
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is not None
    assert witnesses["G6"].source == "artifact:R1"


def test_g6_s9_gate6_verdict_ratify_witnesses(tmp_path: Path) -> None:
    """S9-style **Verdict:** **RATIFY** block witnesses with cited sha."""
    files_root = tmp_path / "cortex"
    review_body = "## Findings\n\nClean.\n\n**Verdict:** **RATIFY**\n"
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is not None


def test_g6_bare_verdict_ratify_line_witnesses(tmp_path: Path) -> None:
    """Whole-line ``VERDICT: RATIFY`` witnesses when cited sha matches."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: RATIFY\n"
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is not None


def test_g6_prose_ratify_withdrawn_verdict_reject_not_witness(tmp_path: Path) -> None:
    """Prose mentions RATIFY; only **Verdict:** **REJECT** binds — no G6 witness."""
    files_root = tmp_path / "cortex"
    review_body = (
        "The prior round's RATIFY is withdrawn after new findings.\n\n"
        "**Verdict:** **REJECT**\n"
    )
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None


def test_g6_quoted_merits_instruction_merits_return_not_witness(tmp_path: Path) -> None:
    """Quoted ``Merits: RATIFY`` in instructions; live Merits: RETURN blocks G6."""
    files_root = tmp_path / "cortex"
    review_body = (
        "Write the sidecar with a line exactly like Merits: RATIFY in the template.\n\n"
        "Merits: RETURN\n\n"
        "**Verdict:** **RETURN**\n"
    )
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None


def test_g6_lowercase_prose_merits_admit_not_witness(tmp_path: Path) -> None:
    """``on the merits: admit`` mid-sentence is not a standalone Merits line."""
    files_root = tmp_path / "cortex"
    review_body = (
        "We discussed this on the merits: admit was the old word.\n\n"
        "No verdict line is present.\n"
    )
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None


def test_g6_pre_admit_heading_verdict_return_not_witness(tmp_path: Path) -> None:
    """Pre-ADMIT review with ``## Verdict: **RETURN**`` does not witness G6."""
    files_root = tmp_path / "cortex"
    review_body = "Pre-ADMIT review — scope only.\n\n## Verdict: **RETURN**\n"
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is None


@pytest.mark.parametrize(
    ("review_body", "expected_reason"),
    [
        ("VERDICT: WITHHOLD\nPending operator.\n", "unrecognized review verdict"),
        ("Merits: ADMIT_WITH_AMENDMENTS\n", "negative review verdict"),
        ("**Verdict:** **REJECT**\n", "negative review verdict"),
    ],
)
def test_g6_non_advance_review_bodies_block(
    tmp_path: Path,
    review_body: str,
    expected_reason: str,
) -> None:
    files_root = tmp_path / "cortex"
    uri = _write_review(files_root, review_body)
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    reason = _g6_review_failure_reason(
        uri,
        files_root=files_root,
        tip_body=_review_tip(cited_sha=cited_sha, body=review_body),
        artifact_id="R1",
    )
    assert reason == expected_reason


def _gated_rows(*rows: tuple[str, str, str, str]) -> str:
    lines = [
        "## Gated deliverables",
        "",
        "| ID | Deliverable | Status | Stops |",
        "|---|---|---|---|",
    ]
    for row_id, label, status, stops in rows:
        lines.append(f"| {row_id} | {label} | {status} | {stops} |")
    return "\n".join(lines) + "\n"


def _write_g4_review(files_root: Path, body: str) -> str:
    path = files_root / "notes/system/reviews/conductor-hop-wait-protocol-g4.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return "cortex://notes/system/reviews/conductor-hop-wait-protocol-g4.md"


def _g4_stops_tip(
    files_root: Path,
    *,
    g4_status: str,
    g4_stops: str,
    g4_body: str | None = None,
    include_g4_sidecar: bool = True,
) -> str:
    sidecars = [
        "## Sidecars",
        "",
        "| ID | Artifact URI | What it is |",
        "|---|---|---|",
    ]
    if include_g4_sidecar and g4_body is not None:
        uri = _write_g4_review(files_root, g4_body)
        sidecars.append(f"| G4 | `{uri}` | G4 verdict |")
    return (
        _gated_rows(("G4", "Skeptic", g4_status, g4_stops)) + "\n".join(sidecars) + "\n"
    )


@pytest.mark.offline
def test_ac_p2_1_g4_stops_row_pinned_blocks(tmp_path: Path) -> None:
    """AC-P2-1 — G4 URI resolves but Stops=ROW_PINNED blocks G4/G5 witnesses."""
    files_root = tmp_path / "cortex"
    tip_body = _g4_stops_tip(
        files_root,
        g4_status="DONE",
        g4_stops="ROW_PINNED",
        g4_body="## Verdict\n\n**CLEAR.**\n",
    )
    deps = _deps(tmp_path)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G4") is None
    assert witnesses.get("G5") is None
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip_body, encoding="utf-8")
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert fold.row_status["G4"] == "CLAIMED"


@pytest.mark.offline
def test_ac_p2_1b_absent_g4_uri_stops_blocks_g5(tmp_path: Path) -> None:
    """AC-P2-1b — absent G4 URI + Stops still blocks G5."""
    files_root = tmp_path / "cortex"
    tip_body = _g4_stops_tip(
        files_root,
        g4_status="OPEN",
        g4_stops="ROW_PINNED",
        g4_body=None,
        include_g4_sidecar=False,
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G5") is None


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("## Verdict\n\n**AMEND — scope drift.**\n", False),
        (
            "Verdict: G4 **does not** clear G5.\nAC-7 | **FAIL**\n",
            False,
        ),
        ("## Verdict\n\n**CLEAR.**\n", True),
        (
            "## Verdict\n\n**CLEAR.** The prior AMEND collapses after review.\n",
            True,
        ),
    ],
)
@pytest.mark.offline
def test_ac_p2_2_g4_body_withhold_param(
    tmp_path: Path, body: str, expected: bool
) -> None:
    """AC-P2-2 — G4 body AMEND withhold via _g4_body_clears."""
    files_root = tmp_path / "cortex"
    uri = _write_g4_review(files_root, body)
    assert _g4_body_clears(uri, files_root=files_root) is expected


@pytest.mark.offline
def test_ac_p2_7_degraded_reasons_imports_stops_helper() -> None:
    """AC-P2-7 — sole Stops parse locus re-exported from degraded_reasons."""
    mod = importlib.import_module("implement_admission.degraded_reasons")
    assert mod.stops_block_reason is stops_block_reason
    assert mod.g4_stops_block_reason("| G4 | x | OPEN | ROW_PINNED |") == "ROW_PINNED"


_BARE_SHA40 = "deadbeef" + "0" * 32


def _sidecar_row(artifact_id: str, sha: str = _BARE_SHA40) -> str:
    return f"| {artifact_id} | {sha} on master | land witness |\n"


def test_r2_c3_uri_resolves_bare_sha40_without_repo_or_files(tmp_path: Path) -> None:
    """R2 C3 — _uri_resolves bare 40-hex branch (restored once; pin it)."""
    files_root = tmp_path / "cortex"
    files_root.mkdir()
    assert _uri_resolves(_BARE_SHA40, files_root=files_root, repo=None) is True


@pytest.mark.parametrize(
    ("artifact_ids", "sidecar_id"),
    [
        (_G2_ARTIFACT_IDS, "F1"),
        (_G3_ARTIFACT_IDS, "S4b"),
        (("G4",), "G4"),
    ],
)
def test_r2_c3_first_resolving_artifact_bare_sha40(
    tmp_path: Path,
    artifact_ids: tuple[str, ...],
    sidecar_id: str,
) -> None:
    """R2 C3 — sidecar bare sha resolves via _first_resolving_artifact."""
    tip_body = "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
    tip_body += _sidecar_row(sidecar_id)
    artifacts = _artifact_map(tip_body)
    assert artifacts[sidecar_id] == _BARE_SHA40
    art_id, uri = _first_resolving_artifact(
        artifacts,
        artifact_ids,
        files_root=tmp_path / "cortex",
        repo=None,
    )
    assert art_id == sidecar_id
    assert uri == _BARE_SHA40


@pytest.mark.parametrize(
    ("sidecar_id", "row_id", "expected_source"),
    [
        ("F1", "G2", "artifact:F1"),
        ("S4b", "G3", "artifact:S4b"),
        ("G4", "G4", "artifact:G4"),
    ],
)
def test_r2_c3_bare_sha40_sidecar_witnesses_g2_g3_g4(
    tmp_path: Path,
    sidecar_id: str,
    row_id: str,
    expected_source: str,
) -> None:
    """R2 C3 — G2–G4 row witnesses accept bare 40-hex sidecar URIs."""
    files_root = tmp_path / "cortex"
    files_root.mkdir()
    tip_body = (
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n"
        "|---|---|---|\n" + _sidecar_row(sidecar_id)
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    witness = witnesses.get(row_id)
    assert witness is not None
    assert witness.source == expected_source
    assert witness.detail == _BARE_SHA40


def test_g3_witness_from_spec_artifact_ignores_density_triage(tmp_path: Path) -> None:
    """G3 witness resolves from spec artifact only (AC4)."""
    files_root = tmp_path / "cortex"
    spec_uri = "cortex://notes/system/specs/conductor-hop-wait-protocol-s4b.md"
    spec_path = files_root / "notes/system/specs/conductor-hop-wait-protocol-s4b.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# spec\n", encoding="utf-8")
    tip_body = (
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n"
        "|---|---|---|\n"
        f"| S4b | `{spec_uri}` | G3 spec |\n"
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path, triage="implement_ready"),
        files_root=files_root,
        rows=G_ROWS,
    )
    g3 = witnesses.get("G3")
    assert g3 is not None
    assert g3.source == "artifact:S4b"
    assert g3.detail == spec_uri
