"""P2 affirmative sha-bound witness fold — AC3–AC6 (a:32391)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_witness import FoldDeps, fold_scoreboard, row_witnesses
from implement_admission.conductor_score_journal import G_ROWS

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
