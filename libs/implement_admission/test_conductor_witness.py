"""Hermetic tests for conductor witnessed-DONE fold."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_materialize import materialize_conductor
from implement_admission.conductor_score_journal import (
    _parse_journal,
    load_journal,
    read_tip,
    walk_journal_to_tip,
)
from implement_admission.conductor_witness import (
    FoldDeps,
    fold_scoreboard,
    row_witnesses,
)
from implement_admission.conductor_witness_defaults import score_resurface_in_turns

pytestmark = pytest.mark.offline

_LIVE_TIP = Path(
    "/mnt/torus/mcp-data/files/notes/system/scoreboards/entity-private-id-mutable-name-scoreboard.md"
)
_LIVE_JOURNAL = Path(
    "/mnt/torus/mcp-data/files/notes/system/scoreboards/entity-private-id-mutable-name-score-journal.md"
)
_SLUG = "entity-private-id-mutable-name"
_SOURCE_REF = f"todo:{_SLUG}"


class _StubCortex:
    def __init__(
        self,
        *,
        attrs: dict[str, Any] | None = None,
        relationships: list[dict[str, Any]] | None = None,
    ) -> None:
        self._attrs = attrs or {"density_triage": "judgment_required"}
        self._relationships = relationships or []

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        if entity_id.startswith("document:"):
            if self._attrs.get("card_shaped"):
                return {
                    "id": entity_id,
                    "summary_row": "consult_kind=architecture. Source artifact C1.",
                    "attributes": {},
                }
            if self._attrs.get("desc_shaped"):
                return {
                    "id": entity_id,
                    "description": "consult_kind=architecture",
                    "attributes": {},
                }
            return {"id": entity_id, "attributes": {"consult_kind": "architecture"}}
        return {"id": entity_id, "attributes": dict(self._attrs)}

    def list_relationships(
        self,
        entity_id: str,
        *,
        type_id: str | None = None,
    ) -> list[dict[str, Any]]:
        _ = entity_id, type_id
        return list(self._relationships)


class _StubBus:
    def __init__(self, *, resurface: bool = False, nested_commits: bool = False) -> None:
        self._resurface = resurface
        self._nested_commits = nested_commits

    def has_score_resurface_after(
        self,
        *,
        thread_id: str,
        after_written_at: str | None,
    ) -> bool:
        _ = thread_id, after_written_at
        return self._resurface

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        _ = nest_under_dispatch_id
        return self._nested_commits


class _StubGit:
    def __init__(self, *, landed: bool = True) -> None:
        self._landed = landed

    def is_ancestor(self, commit: str, ref: str) -> bool:
        _ = commit, ref
        return self._landed


def _seed_live_fixture(files_root: Path) -> None:
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True, exist_ok=True)
    if _LIVE_TIP.is_file():
        shutil.copy(_LIVE_TIP, scoreboards / f"{_SLUG}-scoreboard.md")
    if _LIVE_JOURNAL.is_file():
        shutil.copy(_LIVE_JOURNAL, scoreboards / f"{_SLUG}-score-journal.md")
    for rel in (
        "notes/system/frames/entity-private-id-mutable-name-g2-frame.md",
        "notes/system/specs/entity-private-id-mutable-name.md",
    ):
        src = Path("/mnt/torus/mcp-data/files") / rel
        if src.is_file():
            dest = files_root / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dest)


@pytest.fixture
def live_fixture(tmp_path: Path) -> tuple[Path, Path]:
    files_root = tmp_path / "cortex"
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed_live_fixture(files_root)
    return files_root, repo


def test_parse_journal_ndjson_two_records(live_fixture: tuple[Path, Path]) -> None:
    files_root, _repo = live_fixture
    journal_path = files_root / "notes/system/scoreboards" / f"{_SLUG}-score-journal.md"
    text = journal_path.read_text(encoding="utf-8")
    records = _parse_journal(text)
    assert len(records) >= 2
    assert len(load_journal(_SLUG, files_root=files_root)) == len(records)


def test_fold_live_fixture_entry_gate_and_claimed(live_fixture: tuple[Path, Path]) -> None:
    files_root, repo = live_fixture
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="9638",
        repo=repo,
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root)
    assert fold is not None
    assert fold.entry_gate == "G1"
    assert fold.row_status["G1"] == "CLAIMED"
    assert fold.row_status["G2"] == "DONE"
    assert fold.row_status["G3"] == "DONE"
    assert fold.row_status["G4"] == "CLAIMED"
    assert fold.row_status["G5"] == "CLAIMED"
    # Legacy 6-row tip had G6=land DONE without R1 review; 7-row fold reclassifies.
    assert fold.row_status["G6"] == "CLAIMED"
    journal = load_journal(_SLUG, files_root=files_root)
    assert any(r.get("reason") == "witness_fold" for r in journal)


def test_materialize_lists_missing_witnesses(live_fixture: tuple[Path, Path], tmp_path: Path) -> None:
    files_root, repo = live_fixture
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="9638",
        repo=repo,
    )
    mp = materialize_conductor(
        _SOURCE_REF,
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets",
        files_root=files_root,
        fold_deps=deps,
        caller_agent="cursor",
    )
    assert "Entry gate: G1" in mp.text
    assert "G1 CLAIMED:" in mp.text
    assert "G4 CLAIMED:" in mp.text
    assert "G5 CLAIMED:" in mp.text
    assert "attach witnesses, do not re-derive" in mp.text
    assert "DONE is rendered from witnesses" in mp.text


def test_raw_done_renders_claimed_on_read_tip(live_fixture: tuple[Path, Path]) -> None:
    files_root, repo = live_fixture
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=repo,
    )
    tip = read_tip(_SLUG, files_root=files_root, fold_deps=deps)
    assert tip is not None
    assert "CLAIMED" in tip[0]
    walked = walk_journal_to_tip(_SLUG, files_root=files_root)
    assert walked == tip[1]


def test_derived_from_edge_renders_g1_done(live_fixture: tuple[Path, Path]) -> None:
    files_root, repo = live_fixture
    rel = {
        "id": 42,
        "source_id": _SOURCE_REF,
        "target_id": "document:entity-private-id-architecture",
        "type_id": "derived_from",
    }
    cortex = _StubCortex(relationships=[rel])
    deps = FoldDeps(
        cortex=cortex,
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=repo,
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=(files_root / "notes/system/scoreboards" / f"{_SLUG}-scoreboard.md").read_text(),
        deps=deps,
        files_root=files_root,
    )
    assert witnesses["G1"] is not None
    assert witnesses["G1"].source == "derived_from:42"
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root)
    assert fold is not None
    assert fold.row_status["G1"] == "DONE"


def test_derived_from_edge_reads_consult_kind_from_description() -> None:
    rel = {
        "id": 8442,
        "source_id": _SOURCE_REF,
        "target_id": "document:desc-shaped-architecture",
        "type_id": "derived_from",
    }
    cortex = _StubCortex(attrs={"desc_shaped": True}, relationships=[rel])
    deps = FoldDeps(
        cortex=cortex,
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=Path("/tmp"),
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body="| G1 | Architecture | CLAIMED |\n",
        deps=deps,
        files_root=Path("/tmp"),
    )
    assert witnesses["G1"] is not None
    assert witnesses["G1"].source == "derived_from:8442"


def test_s7_frame_witnesses_g2(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    frames = files_root / "notes/system/frames"
    frames.mkdir(parents=True)
    (frames / "slug-g2-frame.md").write_text("frame", encoding="utf-8")
    tip_body = (
        "| ID | Artifact |\n"
        "|---|---|\n"
        "| S7 | `cortex://notes/system/frames/slug-g2-frame.md` |\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
    )
    assert witnesses["G2"] is not None
    assert witnesses["G2"].source == "artifact:S7"


def test_s9_spec_witnesses_g3(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    specs = files_root / "notes/system/specs"
    specs.mkdir(parents=True)
    (specs / "slug.md").write_text("spec", encoding="utf-8")
    tip_body = (
        "| ID | Artifact |\n"
        "|---|---|\n"
        "| S9 | `cortex://notes/system/specs/slug.md` |\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
    )
    assert witnesses["G3"] is not None
    assert witnesses["G3"].source == "artifact:S9"


def test_tip_g2_cell_uri_is_witness(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    frames = files_root / "notes/system/frames"
    frames.mkdir(parents=True)
    (frames / "hung.md").write_text("frame", encoding="utf-8")
    tip_body = (
        "| G2 | Frame | hung | `cortex://notes/system/frames/hung.md` |\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
    )
    assert witnesses["G2"] is not None
    assert witnesses["G2"].source == "artifact:tip"


def test_derived_from_edge_reads_consult_kind_from_card_summary() -> None:
    rel = {
        "id": 8441,
        "source_id": _SOURCE_REF,
        "target_id": "document:entity-private-id-mutable-name-architecture-consult",
        "type_id": "derived_from",
    }
    cortex = _StubCortex(attrs={"card_shaped": True}, relationships=[rel])
    deps = FoldDeps(
        cortex=cortex,
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=Path("/tmp"),
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body="| G1 | Architecture | CLAIMED |\n",
        deps=deps,
        files_root=Path("/tmp"),
    )
    assert witnesses["G1"] is not None
    assert witnesses["G1"].source == "derived_from:8441"


def test_g4_withhold_body_is_not_a_witness(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    reviews = files_root / "notes/system/reviews"
    reviews.mkdir(parents=True)
    g4_path = reviews / "withhold.md"
    g4_path.write_text(
        "Verdict: G4 **does not** clear G5.\nAC-7 | **FAIL**\n",
        encoding="utf-8",
    )
    tip_body = (
        "| ID | Artifact |\n"
        "|---|---|\n"
        "| G4 | `cortex://notes/system/reviews/withhold.md` |\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
    )
    assert witnesses["G4"] is None


def test_g4_withhold_blocks_g5_even_with_resurface(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    reviews = files_root / "notes/system/reviews"
    reviews.mkdir(parents=True)
    (reviews / "withhold.md").write_text(
        "Verdict: G4 **does not** clear G5.\nwithhold G5 completeness\n",
        encoding="utf-8",
    )
    tip_body = (
        "| G4 | `cortex://notes/system/reviews/withhold.md` |\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(resurface=True),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="9638",
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
    )
    assert witnesses["G4"] is None
    assert witnesses["G5"] is None


def test_g7_land_without_g6_review_has_no_witness(tmp_path: Path) -> None:
    """L1 on master without R1 must not witness G7 (review harvest ≺ land)."""
    files_root = tmp_path / "cortex"
    land_sha = "a" * 40
    tip_body = f"| L1 | {land_sha} |\n"
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(resurface=True),
        git=_StubGit(landed=True),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="9638",
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=files_root,
    )
    assert witnesses.get("G5") is not None
    assert witnesses.get("G6") is None
    assert witnesses.get("G7") is None


def test_score_resurface_in_turns_respects_cutoff() -> None:
    turns = [
        {
            "subject": "SCORE_RESURFACE — slug G3→G5",
            "created_at": "2026-08-26T06:30:00Z",
        }
    ]
    assert score_resurface_in_turns(turns, after_written_at=None) is True
    assert (
        score_resurface_in_turns(turns, after_written_at="2026-08-26T06:00:00Z") is True
    )
    assert (
        score_resurface_in_turns(turns, after_written_at="2026-08-26T07:00:00Z") is False
    )
    assert score_resurface_in_turns(
        [{"subject": "CHECKPOINT 87", "created_at": "2026-08-26T06:30:00Z"}],
        after_written_at=None,
    ) is False


@pytest.mark.offline
def test_ac_p1_4_fold_deps_summoning_thread(tmp_path: Path) -> None:
    """AC-P1-4 — fold_deps_for_admit carries predecessor summoning_thread_id."""
    from implement_admission.conductor_witness_defaults import fold_deps_for_admit

    repo = tmp_path / "repo"
    repo.mkdir()
    deps = fold_deps_for_admit(
        "todo:conductor-admit-integrity",
        cortex=_StubCortex(),
        repo=repo,
        summoning_thread_id="10223",
    )
    assert deps.summoning_thread_id == "10223"


@pytest.mark.offline
def test_ac_p2_3_fold_missing_witnesses_stops(tmp_path: Path) -> None:
    """AC-P2-3 — fold missing_witnesses cites Stops; OPEN rows land in blocked_rows."""
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    reviews = files_root / "notes/system/reviews"
    reviews.mkdir(parents=True)
    g4_uri = "cortex://notes/system/reviews/withhold-stops.md"
    (reviews / "withhold-stops.md").write_text(
        "Verdict: G4 **does not** clear G5.\n",
        encoding="utf-8",
    )
    done_tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        f"| G4 | Skeptic | DONE | ROW_PINNED |\n\n"
        "## Sidecars\n\n| ID | Artifact URI |\n|---|---|\n"
        f"| G4 | `{g4_uri}` |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(done_tip, encoding="utf-8")
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert fold.missing_witnesses.get("G4") == "stops: ROW_PINNED"

    open_tip = done_tip.replace("| G4 | Skeptic | DONE |", "| G4 | Skeptic | OPEN |")
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(open_tip, encoding="utf-8")
    fold_open = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold_open is not None
    assert fold_open.blocked_rows.get("G4") == "ROW_PINNED"
    assert "G4" not in fold_open.missing_witnesses


@pytest.mark.offline
def test_ac_p2_4_score_resurface_no_g5_when_stops_block(tmp_path: Path) -> None:
    """AC-P2-4 — SCORE_RESURFACE alone does not witness G5 when G4 Stops blocks."""
    tip_body = (
        "## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        "| G4 | Skeptic | OPEN | ROW_PINNED |\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(resurface=True),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        summon_mode="attended",
        summoning_thread_id="9638",
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=tmp_path / "cortex",
    )
    assert witnesses.get("G5") is None
