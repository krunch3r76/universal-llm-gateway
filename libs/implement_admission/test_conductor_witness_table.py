"""P2 affirmative sha-bound witness fold — AC3–AC6 (a:32391)."""

from __future__ import annotations

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
from implement_admission.conductor_witness_table import _g4_body_clears
from implement_admission.degraded_reasons import (
    stops_block_reason,
)

pytestmark = pytest.mark.offline

_SLUG = "conductor-hop-wait-protocol-fixture"
_SOURCE_REF = "todo:conductor-hop-wait-protocol"


class _StubCortex:
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {"id": entity_id, "attributes": {"density_triage": "judgment_required"}}

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


def _deps(tmp_path: Path) -> FoldDeps:
    return FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="10110",
        repo=tmp_path / "repo",
    )


def _g5_precondition_tip(files_root: Path, review_body: str) -> str:
    _write_review(files_root, review_body)
    return _review_tip(body=review_body)


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


def test_ac4_ratify_with_conditions_witnesses_g6(tmp_path: Path) -> None:
    """AC4 — VERDICT: RATIFY_WITH_CONDITIONS ⇒ G6 witness."""
    files_root = tmp_path / "cortex"
    review_body = "VERDICT: RATIFY_WITH_CONDITIONS\n\nMinor nits only.\n"
    tip_body = _g5_precondition_tip(files_root, review_body)
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=_deps(tmp_path),
        files_root=files_root,
        rows=G_ROWS,
    )
    assert witnesses.get("G6") is not None
    assert witnesses["G6"].source == "artifact:R1"


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
    return _gated_rows(("G4", "Skeptic", g4_status, g4_stops)) + "\n".join(sidecars) + "\n"


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
def test_ac_p2_2_g4_body_withhold_param(tmp_path: Path, body: str, expected: bool) -> None:
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
