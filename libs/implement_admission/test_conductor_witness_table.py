"""P2 affirmative sha-bound witness fold — AC3–AC6 (a:32391)."""

from __future__ import annotations

import hashlib
import importlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_score_journal import G_ROWS
from implement_admission.conductor_witness import (
    FoldDeps,
    fold_scoreboard,
    row_witnesses,
)
from implement_admission.conductor_witness_g5 import (
    g5_implement_witness,
    hang_g5_witness,
    select_g5_implement_witness,
)
from implement_admission.conductor_witness_table import (
    _G2_ARTIFACT_IDS,
    _G3_ARTIFACT_IDS,
    _artifact_map,
    _conductor_dispatch_id,
    _first_resolving_artifact,
    _g4_body_clears,
    _g6_review_failure_reason,
    _uri_resolves,
)
from implement_admission.degraded_reasons import (
    stops_block_reason,
)

pytestmark = pytest.mark.offline

_RETRY_DISPATCH_ID = "bd8e467f-0c5c-4631-80f8-a13f6548eaba-r1"


def test_conductor_dispatch_id_captures_retry_suffix() -> None:
    tip = f"conductor dispatch_id `{_RETRY_DISPATCH_ID}`\n"
    assert _conductor_dispatch_id(tip) == _RETRY_DISPATCH_ID


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
    def __init__(self, *, resurface: bool = True) -> None:
        self._resurface = resurface

    def has_score_resurface_after(
        self,
        *,
        thread_id: str,
        after_written_at: str | None,
        **kwargs: object,
    ) -> bool:
        _ = thread_id, after_written_at, kwargs
        return self._resurface


class _StubNestedImplement:
    def __init__(self, *, has_commits: bool = False) -> None:
        self._has_commits = has_commits

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        _ = nest_under_dispatch_id
        return self._has_commits


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
        f"\nconductor dispatch_id `12345678-abcd-1234-abcd-123456789abc`\n"
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
        nested_implement=_StubNestedImplement(has_commits=True),
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


def test_g6_archive_verdict_bind_witnesses_with_cited_sha(tmp_path: Path) -> None:
    """Archive ``Verdict: BIND`` is G6-fold-only; shared grammar stays unchanged (a:38233)."""
    files_root = tmp_path / "cortex"
    review_body = (
        "# Pre-land archive\n\n"
        "Verdict: BIND. All four claims match head deadbeef, no new defect.\n"
    )
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    uri = _write_review(files_root, review_body)
    reason = _g6_review_failure_reason(
        uri,
        files_root=files_root,
        tip_body=_review_tip(cited_sha=cited_sha, body=review_body),
        artifact_id="R1",
    )
    assert reason is None
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


def test_g6_pass_period_sentence_missing_cited_sha(tmp_path: Path) -> None:
    """``VERDICT: pass. …`` is advance; a tip row with no sha stays missing_cited_sha."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: pass. B1 is closed.\n"
    uri = _write_review(files_root, review_body)
    reason = _g6_review_failure_reason(
        uri,
        files_root=files_root,
        tip_body=_review_tip(cited_sha=None, body=review_body),
        artifact_id="R1",
    )
    assert reason == "missing_cited_sha"
    assert reason != "unrecognized review verdict"


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
    assert fold.row_status["G4"] == "DONE"


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


def _sidecar_row_git(artifact_id: str, sha: str = _BARE_SHA40) -> str:
    return f"| {artifact_id} | git:{sha} | land witness |\n"


def test_artifact_map_ingests_git_prefixed_l1_sha() -> None:
    """Sidecar ``git:<sha>`` normalizes to bare hex for G7 land slot (a:38233)."""
    tip_body = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + _sidecar_row_git("L1")
    )
    artifacts = _artifact_map(tip_body)
    assert artifacts["L1"] == _BARE_SHA40


class _MasterLandGit:
    def is_ancestor(self, commit: str, ref: str) -> bool:
        return ref == "master" and commit == _BARE_SHA40


def test_g7_witnesses_from_git_prefixed_l1_sidecar(tmp_path: Path) -> None:
    """G7 land reads L1 after G6 binds; ``git:`` prefix must not drop the sha."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: RATIFY\n"
    cited_sha = hashlib.sha256(review_body.encode()).hexdigest()
    tip_body = _g5_precondition_tip(files_root, review_body, cited_sha=cited_sha)
    tip_body += (
        "## Sidecars (land)\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + _sidecar_row_git("L1")
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        nested_implement=_StubNestedImplement(has_commits=True),
        git=_MasterLandGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="10110",
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is not None
    g7 = witnesses.get("G7")
    assert g7 is not None
    assert g7.detail == _BARE_SHA40
    assert g7.source == f"git:{_BARE_SHA40}"


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


def _git_repo_at_commit(tmp_path: Path, *, advance: bool = False) -> tuple[Path, str]:
    """Init a repo; return (repo path, first commit full sha). Optionally advance HEAD."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "witness@test"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "witness"], cwd=repo, check=True)
    (repo / "f").write_text("1\n", encoding="utf-8")
    subprocess.run(["git", "add", "f"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "c1"], cwd=repo, check=True)
    first_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True
    ).strip()
    if advance:
        (repo / "f").write_text("2\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "c2"], cwd=repo, check=True)
    return repo, first_sha


@pytest.mark.offline
def test_g5_git_lane_head_when_head_equals_l1(tmp_path: Path) -> None:
    """confer_and_finish: HEAD == L1 sha witnesses G5 via git:lane_head."""
    repo, l1_sha = _git_repo_at_commit(tmp_path)
    tip_body = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + _sidecar_row("L1", l1_sha)
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        nested_implement=_StubNestedImplement(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="confer_and_finish",
        repo=repo,
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=tmp_path / "cortex",
        rows=G_ROWS,
    )
    g5 = witnesses.get("G5")
    assert g5 is not None
    assert g5.source == "git:lane_head"
    assert g5.detail == l1_sha.lower()


@pytest.mark.offline
def test_g5_git_lane_head_descendant_head_witnesses(tmp_path: Path) -> None:
    """confer_and_finish: lane HEAD descendant of L1 witnesses G5 (ancestry-or-HEAD)."""
    from implement_admission.conductor_witness_defaults import DefaultWitnessGit

    repo, l1_sha = _git_repo_at_commit(tmp_path, advance=True)
    tip_body = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + _sidecar_row("L1", l1_sha)
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        nested_implement=_StubNestedImplement(),
        git=DefaultWitnessGit(repo),
        source_ref=_SOURCE_REF,
        summon_mode="confer_and_finish",
        repo=repo,
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=tmp_path / "cortex",
        rows=G_ROWS,
    )
    g5 = witnesses.get("G5")
    assert g5 is not None
    assert g5.source == "git:lane_head"


@pytest.mark.offline
def test_g5_attended_resurface_without_implement_stays_open(tmp_path: Path) -> None:
    """Attended SCORE_RESURFACE without nest/L1 does not close G5 (a:37198 A1)."""
    repo, l1_sha = _git_repo_at_commit(tmp_path, advance=True)
    tip_body = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + _sidecar_row("L1", l1_sha)
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="10110",
        repo=repo,
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=tmp_path / "cortex",
        rows=G_ROWS,
    )
    assert witnesses.get("G5") is None


@pytest.mark.offline
def test_g5_attended_resurface_and_nested_implement(tmp_path: Path) -> None:
    """Attended G5 hangs only when resurface and nested implement both exist."""
    dispatch_id = "12345678-abcd-1234-abcd-123456789abc"
    repo, l1_sha = _git_repo_at_commit(tmp_path, advance=True)
    tip_body = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + _sidecar_row("L1", l1_sha)
        + f"\nconductor dispatch_id `{dispatch_id}`\n"
    )
    g5 = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=FoldDeps(
            cortex=_StubCortex(),
            bus=_StubBus(),
            nested_implement=_StubNestedImplement(has_commits=True),
            git=_StubGit(),
            source_ref=_SOURCE_REF,
            summon_mode="attended",
            summoning_thread_id="10110",
            repo=repo,
        ),
        files_root=tmp_path / "cortex",
        rows=G_ROWS,
    ).get("G5")
    assert g5 is not None
    assert g5.source == "bus:SCORE_RESURFACE"


@pytest.mark.offline
def test_g5_attended_nested_implement_without_resurface(tmp_path: Path) -> None:
    """Attended nested_implement must not close G5 without SCORE_RESURFACE (A2)."""
    dispatch_id = "12345678-abcd-1234-abcd-123456789abc"
    tip_body = f"conductor dispatch_id `{dispatch_id}`\n"
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=FoldDeps(
            cortex=_StubCortex(),
            bus=_StubBus(resurface=False),
            nested_implement=_StubNestedImplement(has_commits=True),
            git=_StubGit(),
            source_ref=_SOURCE_REF,
            summon_mode="attended",
            summoning_thread_id="10110",
            repo=tmp_path / "repo",
        ),
        files_root=tmp_path / "cortex",
        rows=G_ROWS,
    )
    assert witnesses.get("G5") is None


class _WorkKeyNestedImplement:
    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        _ = nest_under_dispatch_id
        return False

    def parent_with_commits(
        self,
        *,
        tip_body: str,
        explicit_parent_id: str | None,
        work_key: str | None = None,
    ) -> str | None:
        _ = tip_body
        if explicit_parent_id:
            return None
        if work_key == _SOURCE_REF:
            return "parent-from-work-key"
        return None


@pytest.mark.offline
def test_g5_implement_uses_work_key_when_tip_omits_dispatch_id(
    tmp_path: Path,
) -> None:
    """Falsifier a:37920 — nested commits witness G5 with the pin line deleted."""
    tip_body = "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
    g5 = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=FoldDeps(
            cortex=_StubCortex(),
            nested_implement=_WorkKeyNestedImplement(),
            source_ref=_SOURCE_REF,
            summon_mode="away",
            repo=tmp_path / "repo",
        ),
        files_root=tmp_path / "cortex",
        rows=G_ROWS,
    ).get("G5")
    assert g5 is not None
    assert g5.source == "ledger:nested_implement"
    assert g5.detail == "parent-from-work-key"


_G5_CORTEX_URI_A = "cortex://notes/system/specs/g5-cortex-fixture-a.md"
_G5_CORTEX_URI_B = "cortex://notes/system/specs/g5-cortex-fixture-b.md"
_G5_CORTEX_G3_URI = "cortex://notes/system/specs/g5-cortex-fixture-g3-s4b.md"


def _write_cortex_uri(files_root: Path, uri: str, body: str) -> str:
    rel = uri.removeprefix("cortex://")
    path = files_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return hashlib.sha256(body.encode()).hexdigest()


def _g3_cortex_only_spec_body(
    entries: list[tuple[str, str]],
    *,
    extra_lines: list[str] | None = None,
) -> str:
    lines = ["# G3 cortex-only fixture\n", "\n", "## files_expected\n", "\n"]
    for uri, pre_sha in entries:
        lines.append(f"- `{uri}`\n")
        lines.append(f"  pre-edit read_sha256: {pre_sha}\n")
    if extra_lines:
        lines.extend(extra_lines)
    return "".join(lines)


def _g5_cortex_scenario(
    files_root: Path,
    *,
    file_bodies: dict[str, str],
    g3_entries: list[tuple[str, str]],
    tip_sha_citations: list[str],
    g3_extra: list[str] | None = None,
) -> tuple[str, dict[str, str]]:
    g3_body = _g3_cortex_only_spec_body(g3_entries, extra_lines=g3_extra)
    _write_cortex_uri(files_root, _G5_CORTEX_G3_URI, g3_body)
    for uri, body in file_bodies.items():
        _write_cortex_uri(files_root, uri, body)
    tip_body = (
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n"
        "|---|---|---|\n"
        f"| S4b | `{_G5_CORTEX_G3_URI}` | G3 spec |\n"
    )
    for sha in tip_sha_citations:
        tip_body += f"\nread_sha256: {sha}\n"
    artifacts = _artifact_map(tip_body)
    return tip_body, artifacts


def _g5_cortex_deps(
    tmp_path: Path,
    *,
    summon_mode: str = "attended",
    resurface: bool = True,
) -> FoldDeps:
    return FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(resurface=resurface),
        nested_implement=_StubNestedImplement(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode=summon_mode,
        summoning_thread_id="10110",
        repo=tmp_path / "repo",
    )


@pytest.mark.offline
def test_g5_cortex_only_attended_resurface_and_post_g3_transition_witnesses(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "baseline-a\n")
    post_a = hashlib.sha256(b"post-g3-a\n").hexdigest()
    tip_body, artifacts = _g5_cortex_scenario(
        files_root,
        file_bodies={_G5_CORTEX_URI_A: "post-g3-a\n"},
        g3_entries=[(_G5_CORTEX_URI_A, pre_a)],
        tip_sha_citations=[post_a],
    )
    deps = _g5_cortex_deps(tmp_path, summon_mode="attended", resurface=True)
    assert (
        g5_implement_witness(
            tip_body=tip_body, deps=deps, artifacts=artifacts, repo=deps.repo
        )
        is None
    )
    implement = select_g5_implement_witness(
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        artifacts=artifacts,
        repo=deps.repo,
    )
    assert implement is not None
    assert implement.source == "cortex:files_expected"
    assert implement.detail == f"{_G5_CORTEX_URI_A}={post_a}"
    g5 = hang_g5_witness(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        artifacts=artifacts,
        repo=deps.repo,
        g4_blocked=False,
    )
    assert g5 is not None
    assert g5.source == "bus:SCORE_RESURFACE"
    assert ";cortex:files_expected:" in g5.detail


@pytest.mark.offline
def test_g5_cortex_only_away_post_g3_transition_witnesses(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "baseline-a\n")
    post_a = hashlib.sha256(b"post-g3-away\n").hexdigest()
    tip_body, artifacts = _g5_cortex_scenario(
        files_root,
        file_bodies={_G5_CORTEX_URI_A: "post-g3-away\n"},
        g3_entries=[(_G5_CORTEX_URI_A, pre_a)],
        tip_sha_citations=[post_a],
    )
    deps = _g5_cortex_deps(tmp_path, summon_mode="confer_and_finish")
    g5 = hang_g5_witness(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
        artifacts=artifacts,
        repo=deps.repo,
        g4_blocked=False,
    )
    assert g5 is not None
    assert g5.source == "cortex:files_expected"
    assert g5.detail == f"{_G5_CORTEX_URI_A}={post_a}"


@pytest.mark.offline
def test_g5_cortex_only_bare_tip_hash_without_post_g3_transition_returns_none(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "still-baseline\n")
    tip_body, artifacts = _g5_cortex_scenario(
        files_root,
        file_bodies={_G5_CORTEX_URI_A: "still-baseline\n"},
        g3_entries=[(_G5_CORTEX_URI_A, pre_a)],
        tip_sha_citations=[pre_a],
    )
    deps = _g5_cortex_deps(tmp_path)
    assert (
        select_g5_implement_witness(
            tip_body=tip_body,
            deps=deps,
            files_root=files_root,
            artifacts=artifacts,
            repo=deps.repo,
        )
        is None
    )
    assert (
        hang_g5_witness(
            _SLUG,
            tip_body=tip_body,
            deps=deps,
            files_root=files_root,
            artifacts=artifacts,
            repo=deps.repo,
            g4_blocked=False,
        )
        is None
    )
    g3_body_no_pre = (
        "# G3\n\n## files_expected\n\n" f"- `{_G5_CORTEX_URI_A}`\n"
    )
    post_a = hashlib.sha256(b"mutated\n").hexdigest()
    _write_cortex_uri(files_root, _G5_CORTEX_G3_URI, g3_body_no_pre)
    _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "mutated\n")
    tip_no_pre = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        f"| S4b | `{_G5_CORTEX_G3_URI}` | G3 spec |\n"
        f"\nread_sha256: {post_a}\n"
    )
    assert (
        select_g5_implement_witness(
            tip_body=tip_no_pre,
            deps=deps,
            files_root=files_root,
            artifacts=_artifact_map(tip_no_pre),
            repo=deps.repo,
        )
        is None
    )


@pytest.mark.offline
def test_g5_cortex_only_tip_hash_mismatch_live_bytes_returns_none(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "baseline-a\n")
    live_sha = hashlib.sha256(b"live-bytes\n").hexdigest()
    wrong_tip_sha = hashlib.sha256(b"stale-tip\n").hexdigest()
    tip_body, artifacts = _g5_cortex_scenario(
        files_root,
        file_bodies={_G5_CORTEX_URI_A: "live-bytes\n"},
        g3_entries=[(_G5_CORTEX_URI_A, pre_a)],
        tip_sha_citations=[wrong_tip_sha],
    )
    deps = _g5_cortex_deps(tmp_path)
    assert live_sha != wrong_tip_sha
    assert (
        select_g5_implement_witness(
            tip_body=tip_body,
            deps=deps,
            files_root=files_root,
            artifacts=artifacts,
            repo=deps.repo,
        )
        is None
    )


@pytest.mark.offline
def test_g5_cortex_only_partial_multi_file_transition_returns_none(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "baseline-a\n")
    pre_b = _write_cortex_uri(files_root, _G5_CORTEX_URI_B, "baseline-b\n")
    post_a = hashlib.sha256(b"post-a\n").hexdigest()
    tip_body, artifacts = _g5_cortex_scenario(
        files_root,
        file_bodies={
            _G5_CORTEX_URI_A: "post-a\n",
            _G5_CORTEX_URI_B: "baseline-b\n",
        },
        g3_entries=[(_G5_CORTEX_URI_A, pre_a), (_G5_CORTEX_URI_B, pre_b)],
        tip_sha_citations=[post_a],
    )
    deps = _g5_cortex_deps(tmp_path)
    assert (
        select_g5_implement_witness(
            tip_body=tip_body,
            deps=deps,
            files_root=files_root,
            artifacts=artifacts,
            repo=deps.repo,
        )
        is None
    )
    _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "post-a\n")
    _write_cortex_uri(files_root, _G5_CORTEX_URI_B, "post-b\n")
    g3_body = _g3_cortex_only_spec_body(
        [(_G5_CORTEX_URI_A, pre_a), (_G5_CORTEX_URI_B, pre_b)]
    )
    _write_cortex_uri(files_root, _G5_CORTEX_G3_URI, g3_body)
    tip_missing_b = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        f"| S4b | `{_G5_CORTEX_G3_URI}` | G3 spec |\n"
        f"\nread_sha256: {post_a}\n"
    )
    assert (
        select_g5_implement_witness(
            tip_body=tip_missing_b,
            deps=deps,
            files_root=files_root,
            artifacts=_artifact_map(tip_missing_b),
            repo=deps.repo,
        )
        is None
    )


@pytest.mark.offline
def test_g5_mixed_repo_and_cortex_files_expected_refuses_cortex_witness(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "baseline-a\n")
    post_a = hashlib.sha256(b"post-a\n").hexdigest()
    _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "post-a\n")
    g3_body = _g3_cortex_only_spec_body(
        [(_G5_CORTEX_URI_A, pre_a)],
        extra_lines=["- `libs/implement_admission/mixed.py`\n"],
    )
    _write_cortex_uri(files_root, _G5_CORTEX_G3_URI, g3_body)
    tip_body = (
        "## Sidecars\n\n| ID | Artifact URI | What it is |\n|---|---|---|\n"
        f"| S4b | `{_G5_CORTEX_G3_URI}` | G3 spec |\n"
        f"\nread_sha256: {post_a}\n"
    )
    deps = _g5_cortex_deps(tmp_path)
    assert (
        select_g5_implement_witness(
            tip_body=tip_body,
            deps=deps,
            files_root=files_root,
            artifacts=_artifact_map(tip_body),
            repo=deps.repo,
        )
        is None
    )


@pytest.mark.offline
def test_g5_cortex_only_attended_without_resurface_returns_none(
    tmp_path: Path,
) -> None:
    files_root = tmp_path / "cortex"
    pre_a = _write_cortex_uri(files_root, _G5_CORTEX_URI_A, "baseline-a\n")
    post_a = hashlib.sha256(b"post-g3-a\n").hexdigest()
    tip_body, artifacts = _g5_cortex_scenario(
        files_root,
        file_bodies={_G5_CORTEX_URI_A: "post-g3-a\n"},
        g3_entries=[(_G5_CORTEX_URI_A, pre_a)],
        tip_sha_citations=[post_a],
    )
    deps = _g5_cortex_deps(tmp_path, summon_mode="attended", resurface=False)
    assert (
        hang_g5_witness(
            _SLUG,
            tip_body=tip_body,
            deps=deps,
            files_root=files_root,
            artifacts=artifacts,
            repo=deps.repo,
            g4_blocked=False,
        )
        is None
    )
