"""Hermetic tests for conductor witnessed-DONE fold."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_materialize import materialize_conductor
from implement_admission.conductor_score_io import _parse_journal
from implement_admission.conductor_score_journal import (
    birth_scoreboard,
    forward_mutate_tip,
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
from implement_admission.conductor_witness_types import row_status_in_tip

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
    def __init__(self, *, resurface: bool = False) -> None:
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


def test_fold_live_fixture_entry_gate_and_claimed(
    live_fixture: tuple[Path, Path],
) -> None:
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


def test_materialize_lists_missing_witnesses(
    live_fixture: tuple[Path, Path], tmp_path: Path
) -> None:
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
    assert (
        "The continuity card ## Skills lists slugs. After resume, read that section "
        "and Use each slug before the first move."
    ) in mp.text


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
        tip_body=(
            files_root / "notes/system/scoreboards" / f"{_SLUG}-scoreboard.md"
        ).read_text(),
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


def test_f1_bare_cortex_uri_witnesses_g2(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    specs = files_root / "notes/system/specs"
    specs.mkdir(parents=True)
    (specs / "slug-g2-frame.md").write_text("frame", encoding="utf-8")
    tip_body = (
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n"
        "|---|---|---|\n"
        "| F1 | cortex://notes/system/specs/slug-g2-frame.md | G2 frame witness |\n"
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
    assert witnesses["G2"].source == "artifact:F1"


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
        "| ID | Artifact |\n|---|---|\n| S9 | `cortex://notes/system/specs/slug.md` |\n"
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
    tip_body = "| G2 | Frame | hung | `cortex://notes/system/frames/hung.md` |\n"
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
    tip_body = "| G4 | `cortex://notes/system/reviews/withhold.md` |\n"
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
    assert witnesses.get("G5") is None
    assert witnesses.get("G6") is None
    assert witnesses.get("G7") is None


def test_score_resurface_in_turns_respects_cutoff() -> None:
    turns = [
        {
            "subject": "SCORE_RESURFACE — slug G3→G5",
            "created_at": "2026-08-26T06:30:00Z",
        }
    ]
    assert score_resurface_in_turns(turns, after_written_at=None, slug="slug") is True
    assert (
        score_resurface_in_turns(
            turns, after_written_at="2026-08-26T06:00:00Z", slug="slug"
        )
        is True
    )
    assert (
        score_resurface_in_turns(
            turns, after_written_at="2026-08-26T07:00:00Z", slug="slug"
        )
        is False
    )
    assert (
        score_resurface_in_turns(
            [{"subject": "CHECKPOINT 87", "created_at": "2026-08-26T06:30:00Z"}],
            after_written_at=None,
            slug="slug",
        )
        is False
    )


def test_score_resurface_in_turns_binds_slug_and_exec() -> None:
    turns = [
        {
            "subject": "SCORE_RESURFACE — other-mission G5",
            "body": "cdp exec 26a259ca-a447-407e-bc91-57eda2e5b0e5",
            "created_at": "2026-08-26T06:30:00Z",
        }
    ]
    assert (
        score_resurface_in_turns(turns, after_written_at=None, slug="this-mission")
        is False
    )
    bound = [
        {
            "subject": "SCORE_RESURFACE — this-mission G5",
            "body": (
                "cdp exec 26a259ca-a447-407e-bc91-57eda2e5b0e5 "
                "read_sha256 abcdef0123456789abcdef0123456789"
            ),
            "created_at": "2026-08-26T06:30:00Z",
        }
    ]
    assert (
        score_resurface_in_turns(
            bound,
            after_written_at=None,
            slug="this-mission",
            exec_id="26a259ca-a447-407e-bc91-57eda2e5b0e5",
            review_sha="abcdef0123456789abcdef0123456789",
        )
        is True
    )
    assert (
        score_resurface_in_turns(
            bound,
            after_written_at=None,
            slug="this-mission",
            exec_id="00000000-0000-0000-0000-000000000000",
        )
        is False
    )


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
    fold_open = fold_scoreboard(
        _SLUG, deps=deps, files_root=files_root, write_journal=False
    )
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


@pytest.mark.offline
def test_default_witness_bus_has_no_services_import() -> None:
    """FoldDeps default bus must not import services.git_integration_worker."""
    from implement_admission.conductor_witness_defaults import DefaultWitnessBus

    bus = DefaultWitnessBus()
    assert not hasattr(bus, "nested_implement_has_commits")
    source = Path(__file__).resolve().parents[0] / "conductor_witness_defaults.py"
    text = source.read_text(encoding="utf-8")
    assert "services.git_integration_worker" not in text


@pytest.mark.offline
def test_default_witness_bus_unknown_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from implement_admission.conductor_witness_defaults import DefaultWitnessBus

    monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
    assert (
        DefaultWitnessBus().has_score_resurface_after(
            thread_id="14162", after_written_at=None, slug="x"
        )
        == "unknown"
    )


@pytest.mark.offline
def test_nested_implement_injection_witnesses_g5(tmp_path: Path) -> None:
    """Injected nested_implement reader witnesses G5 without services import."""
    dispatch_id = "12345678-abcd-1234-abcd-123456789abc"
    tip_body = (
        "## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        "| G4 | Skeptic | OPEN | |\n\n"
        f"conductor dispatch_id `{dispatch_id}`\n"
    )
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        nested_implement=_StubNestedImplement(has_commits=True),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    witnesses = row_witnesses(
        _SLUG,
        tip_body=tip_body,
        deps=deps,
        files_root=tmp_path / "cortex",
    )
    g5 = witnesses.get("G5")
    assert g5 is not None
    assert g5.source == "ledger:nested_implement"
    assert g5.detail == dispatch_id


def _g1_witness_deps(repo: Path) -> FoldDeps:
    rel = {
        "id": 42,
        "source_id": _SOURCE_REF,
        "target_id": "document:entity-private-id-architecture",
        "type_id": "derived_from",
    }
    return FoldDeps(
        cortex=_StubCortex(relationships=[rel]),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=repo,
    )


@pytest.mark.offline
def test_b0_witnessed_g1_folds_status_preserves_mode(tmp_path: Path) -> None:
    """B0-1 — witnessed G1 writes Status, not Mode (5-column sparse-born board).

    Regression pin: pre-fix ``_render_folded_body`` at ``621adef9`` (HEAD before
    ``acfd4de7``) wrote cell index 3 (Mode) on this fixture; ``row_status_in_tip``
    then read OPEN while Mode showed DONE.
    """
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Mode | Status | Stops |\n|---|---|---|---|---|\n"
        "| G1 | Architecture | — | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    fold = fold_scoreboard(
        _SLUG,
        deps=_g1_witness_deps(tmp_path / "repo"),
        files_root=files_root,
        write_journal=False,
    )
    assert fold is not None
    row = "| G1 | Architecture | — | DONE | |"
    assert row in fold.folded_body
    assert row_status_in_tip(fold.folded_body, "G1") == "DONE"
    mode_cell = [p.strip() for p in row.split("|")][3]
    assert mode_cell == "—"


@pytest.mark.offline
def test_b0_witnessed_g1_canonical_referent_header(tmp_path: Path) -> None:
    """B0-1 — 7445-style header: Status column write preserves Canonical referent."""
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    referent = "cortex://notes/system/specs/example.md"
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Canonical referent | Status | Gate / Evidence |\n"
        "|---|---|---|---|---|\n"
        f"| G1 | Architecture | {referent} | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    fold = fold_scoreboard(
        _SLUG,
        deps=_g1_witness_deps(tmp_path / "repo"),
        files_root=files_root,
        write_journal=False,
    )
    assert fold is not None
    assert row_status_in_tip(fold.folded_body, "G1") == "DONE"
    g1_line = next(
        line
        for line in fold.folded_body.splitlines()
        if line.lstrip().startswith("| G1 |")
    )
    parts = [p.strip() for p in g1_line.split("|")]
    assert parts[3] == referent
    assert parts[4] == "DONE"


@pytest.mark.offline
def test_b0_witnessed_g1_legacy_four_column_header(tmp_path: Path) -> None:
    """B0-1 — legacy 4-column header still folds witnessed G1 to DONE."""
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        "| G1 | Architecture | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    fold = fold_scoreboard(
        _SLUG,
        deps=_g1_witness_deps(tmp_path / "repo"),
        files_root=files_root,
        write_journal=False,
    )
    assert fold is not None
    assert "| G1 | Architecture | DONE | |" in fold.folded_body
    assert row_status_in_tip(fold.folded_body, "G1") == "DONE"


@pytest.mark.offline
def test_b0_no_status_header_table_unchanged(tmp_path: Path) -> None:
    """B0-2 — table without Status header is returned unchanged; no journal."""
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Canonical referent | Gate / Evidence |\n"
        "|---|---|---|---|\n"
        "| G1 | Architecture | — | pending |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    before = len(load_journal(_SLUG, files_root=files_root))
    fold = fold_scoreboard(
        _SLUG,
        deps=_g1_witness_deps(tmp_path / "repo"),
        files_root=files_root,
        write_journal=True,
    )
    assert fold is not None
    assert fold.folded_body == fold.raw_body
    assert fold.journal_applied is False
    assert len(load_journal(_SLUG, files_root=files_root)) == before


@pytest.mark.offline
def test_b0_consecutive_folds_one_journal_record(tmp_path: Path) -> None:
    """B0-4 — unchanged witnesses: second fold is idempotent; one journal record total."""
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Mode | Status | Stops |\n|---|---|---|---|---|\n"
        "| G1 | Architecture | — | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    deps = _g1_witness_deps(tmp_path / "repo")
    first = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=True)
    assert first is not None
    assert first.journal_applied is True
    after_first = len(load_journal(_SLUG, files_root=files_root))
    assert after_first >= 1
    second = fold_scoreboard(
        _SLUG, deps=deps, files_root=files_root, write_journal=True
    )
    assert second is not None
    assert second.folded_body == second.raw_body
    assert second.journal_applied is False
    assert len(load_journal(_SLUG, files_root=files_root)) == after_first


@pytest.mark.offline
def test_b0_five_column_tip_folds_status_and_reads_stops(tmp_path: Path) -> None:
    """B0 (a:33504) — sparse-born boards fold the Status cell and keep Mode intact.

    Regression pin: pre-fix ``_render_folded_body`` at ``621adef9`` clobbered Mode
    on this fixture (see ``test_b0_witnessed_g1_folds_status_preserves_mode``).
    """
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Mode | Status | Stops |\n|---|---|---|---|---|\n"
        "| G3 | Densify | plan | OPEN | |\n"
        "| G4 | Skeptic | — | OPEN | ROW_PINNED |\n"
        "| G5 | Implement | agent | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    deps = FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert fold.row_status["G3"] == "OPEN"
    assert fold.blocked_rows.get("G4") == "ROW_PINNED"
    assert "| G5 | Implement | agent | OPEN | |" in fold.folded_body


class _CardOnlyCortex(_StubCortex):
    """Production-shaped reader: the Card projection carries no ``attributes``."""

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003
        if kwargs.get("intent", "card") == "card":
            return {"id": entity_id, "type": "todo", "summary_row": "S4a identity."}
        return super().entity_get(entity_id, **kwargs)


def _r_row_tip(bind_uri: str) -> str:
    return (
        "# Scoreboard — todo:slug\n\n"
        "- **Entry gate:** R1\n\n"
        "## Gated deliverables\n\n"
        "| ID | Deliverable | Mode | Status | Stops |\n|---|---|---|---|---|\n"
        "| R1 | First acceptance row. | — | OPEN | witness hung |\n"
        "| R2 | Second acceptance row. | — | OPEN | |\n"
        "| R3 | Third acceptance row. | — | OPEN | |\n\n"
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n|---|---|---|\n"
        f"| R1-BIND | {bind_uri} | bind witness |\n"
        "| R1-LAND | a6ce58e82263778f704d2b5324efe33da1321ea5 | lane commit, not on master |\n"
        "| R2-BIND | (pending) | bind witness slot |\n"
    )


def test_rows_in_tip_reads_only_the_gated_table() -> None:
    tip = _r_row_tip("cortex://notes/system/scoreboards/slug-r1-bind.md")
    from implement_admission.conductor_witness import rows_in_tip

    assert rows_in_tip(tip) == ("R1", "R2", "R3")
    assert rows_in_tip("no table here") == ()


def test_fold_r_row_mission_follows_tip_rows_when_card_lacks_attributes(
    tmp_path: Path,
) -> None:
    """Worker 13713 (2026-10-01): three hops hung R1/R2/R3 binds and were parked
    for "no progress" because the fold asked the Card for rows, got none, and
    folded the G-ladder — R-BIND witnesses were never read.
    """
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    bind_rel = "notes/system/scoreboards/slug-r1-bind.md"
    (files_root / bind_rel).write_text("# R1 bind\n\nVERDICT: BIND\n", encoding="utf-8")
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(
        _r_row_tip(f"cortex://{bind_rel}"), encoding="utf-8"
    )
    deps = FoldDeps(
        cortex=_CardOnlyCortex(attrs={"density_triage": "judgment_required"}),
        bus=_StubBus(),
        git=_StubGit(landed=False),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert tuple(fold.row_status) == ("R1", "R2", "R3")
    assert fold.witnessed_done == frozenset({"R1"})
    assert fold.row_status["R1"] == "DONE"
    assert fold.entry_gate == "R2"


def test_fold_all_g_tip_keeps_ladder_semantics(tmp_path: Path) -> None:
    """A sparse board that lists only some G rows still folds as the G-ladder."""
    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Mode | Status | Stops |\n|---|---|---|---|---|\n"
        "| G3 | Densify | plan | OPEN | |\n"
        "| G5 | Implement | agent | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    deps = FoldDeps(
        cortex=_CardOnlyCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert tuple(fold.row_status) == ("G1", "G2", "G3", "G4", "G5", "G6", "G7")


def test_mixed_g_and_r_tip_keeps_g4_verdict_check(tmp_path: Path) -> None:
    """One R row must not fold G4 under custom-row rules.

    Custom BIND accepts a resolving URI. The G-ladder refuses a G4 body that
    withholds or FAILs. A conductor-written tip can add an R row beside the
    ladder; that mix still has to keep the G4 check.
    """
    files_root = tmp_path / "cortex"
    reviews = files_root / "notes/system/reviews"
    reviews.mkdir(parents=True)
    (reviews / "withhold.md").write_text(
        "Verdict: G4 **does not** clear G5.\nAC-7 | **FAIL**\n",
        encoding="utf-8",
    )
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Mode | Status | Stops |\n|---|---|---|---|---|\n"
        "| G4 | Skeptic | — | OPEN | |\n"
        "| R1 | Extra acceptance row. | — | OPEN | |\n\n"
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n|---|---|---|\n"
        "| G4 | `cortex://notes/system/reviews/withhold.md` | withhold body |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    deps = FoldDeps(
        cortex=_CardOnlyCortex(),
        bus=_StubBus(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert tuple(fold.row_status) == ("G1", "G2", "G3", "G4", "G5", "G6", "G7")
    assert "G4" not in fold.witnessed_done
    assert fold.row_status["G4"] != "DONE"


_REROUTE_RATIFY = "VERDICT: RATIFY\n"
_G4_CLEAR = "G4 clears G5.\n"
_G4_WITHHOLD = "Verdict: G4 **does not** clear G5.\nAC-7 | **FAIL**\n"


def _sha_file(files_root: Path, rel: str, body: str) -> tuple[str, str]:
    path = files_root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return f"cortex://{rel}", hashlib.sha256(body.encode()).hexdigest()


def _sidecar(artifact_id: str, uri: str, digest: str) -> str:
    return f"| {artifact_id} | `{uri}` `sha256:{digest}` | review |\n"


def _gated_tip(*, g4_stops: str, g6_stops: str, sidecars: str) -> str:
    return (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        "| G1 | frame | OPEN | |\n"
        "| G2 | rival | OPEN | |\n"
        "| G3 | spec | OPEN | |\n"
        "| G4 | skeptic | OPEN | " + g4_stops + " |\n"
        "| G5 | implement | OPEN | |\n"
        "| G6 | review | OPEN | " + g6_stops + " |\n"
        "| G7 | land | OPEN | |\n\n"
        "## Sidecars\n\n"
        "| ID | Artifact URI | What it is |\n|---|---|---|\n"
        + sidecars
        + "\nconductor dispatch_id `12345678-abcd-1234-abcd-123456789abc`\n"
    )


def _reroute_deps(tmp_path: Path) -> FoldDeps:
    return FoldDeps(
        cortex=_StubCortex(),
        bus=_StubBus(resurface=True),
        nested_implement=_StubNestedImplement(has_commits=True),
        git=_StubGit(landed=False),
        source_ref="todo:reroute-verdict",
        summon_mode="attended",
        summoning_thread_id="13691",
        repo=tmp_path / "repo",
    )


def test_g6_reroute_prefers_stamped_route_and_journal_names_it(tmp_path: Path) -> None:
    """Two G6 artifacts: the Stops route wins, and the journal delta names it.

    The operator artifact is listed first so document order cannot govern.
    """
    files_root = tmp_path / "cortex"
    nested_uri, nested_sha = _sha_file(
        files_root,
        "notes/system/reviews/g6-nested-grok.md",
        _REROUTE_RATIFY + "nested-grok\n",
    )
    operator_uri, operator_sha = _sha_file(
        files_root,
        "notes/system/reviews/g6-operator.md",
        _REROUTE_RATIFY + "operator\n",
    )
    tip = _gated_tip(
        g4_stops="",
        g6_stops="cdp_fail_route=nested-grok",
        sidecars=(
            _sidecar("G6-REVIEW-operator", operator_uri, operator_sha)
            + _sidecar("G6-REVIEW-nested-grok", nested_uri, nested_sha)
        ),
    )
    slug = "reroute-verdict-prefer"
    birth_scoreboard(slug, scoreboard_body=tip, files_root=files_root)
    fold = fold_scoreboard(
        slug, deps=_reroute_deps(tmp_path), files_root=files_root, write_journal=True
    )
    assert fold is not None
    assert fold.witnesses["G6"] is not None
    assert fold.witnesses["G6"].detail == nested_uri
    assert (
        fold.witnesses["G6"].source
        == "witness:BIND:G6-REVIEW-nested-grok:route=nested-grok"
    )
    journal = load_journal(slug, files_root=files_root)
    fold_records = [row for row in journal if row.get("reason") == "witness_fold"]
    assert fold_records
    assert "G6 OPEN→DONE [route=nested-grok]" in str(fold_records[-1].get("delta"))


def test_g6_fold_keeps_route_while_second_artifact_is_posted(tmp_path: Path) -> None:
    """Fold, then a conductor posts a late G6 artifact, then fold again.

    Interleaving: the first fold sees only the stamped route's artifact. The
    conductor then appends a torn ``G6-REVIEW-opera`` line and a complete
    commentary artifact. The second fold still binds the stamped URI. A later
    Stops change to ``operator`` is the only thing that switches the witness.
    """
    files_root = tmp_path / "cortex"
    nested_uri, nested_sha = _sha_file(
        files_root,
        "notes/system/reviews/g6-keep-nested.md",
        _REROUTE_RATIFY + "keep-nested\n",
    )
    operator_uri, operator_sha = _sha_file(
        files_root,
        "notes/system/reviews/g6-keep-operator.md",
        _REROUTE_RATIFY + "keep-operator\n",
    )
    tip = _gated_tip(
        g4_stops="",
        g6_stops="cdp_fail_route=nested-grok",
        sidecars=_sidecar("G6-REVIEW-nested-grok", nested_uri, nested_sha),
    )
    slug = "reroute-verdict-interleave"
    birth_scoreboard(slug, scoreboard_body=tip, files_root=files_root)
    deps = _reroute_deps(tmp_path)
    first = fold_scoreboard(slug, deps=deps, files_root=files_root, write_journal=True)
    assert first is not None
    assert first.witnesses["G6"] is not None
    assert first.witnesses["G6"].detail == nested_uri
    current = read_tip(slug, files_root=files_root)
    assert current is not None
    partial = "| G6-REVIEW-opera\n"
    posted = (
        current[0]
        + partial
        + _sidecar("G6-REVIEW-operator", operator_uri, operator_sha)
    )
    mutated = forward_mutate_tip(
        slug,
        next_body=posted,
        seat="conductor",
        dispatch_id=None,
        reason="post second G6 artifact",
        rows=("G6",),
        delta="commentary artifact posted",
        files_root=files_root,
    )
    assert mutated.rejected_reason is None
    second = fold_scoreboard(slug, deps=deps, files_root=files_root, write_journal=True)
    assert second is not None
    assert second.witnesses["G6"] is not None
    assert second.witnesses["G6"].detail == nested_uri
    assert second.witnesses["G6"].detail != operator_uri
    stamped = read_tip(slug, files_root=files_root)
    assert stamped is not None
    switched = stamped[0].replace(
        "cdp_fail_route=nested-grok", "cdp_fail_route=operator", 1
    )
    switched_write = forward_mutate_tip(
        slug,
        next_body=switched,
        seat="conductor",
        dispatch_id=None,
        reason="route stamp changed",
        rows=("G6",),
        delta="cdp_fail_route=operator",
        files_root=files_root,
    )
    assert switched_write.rejected_reason is None
    third = fold_scoreboard(slug, deps=deps, files_root=files_root, write_journal=False)
    assert third is not None
    assert third.witnesses["G6"] is not None
    assert third.witnesses["G6"].detail == operator_uri
    assert (
        third.witnesses["G6"].source == "witness:BIND:G6-REVIEW-operator:route=operator"
    )


def test_g6_stamped_route_does_not_accept_r1_or_other_route(tmp_path: Path) -> None:
    """A route stamp must not fall through to R1 or the other route's artifact."""
    files_root = tmp_path / "cortex"
    r1_uri, r1_sha = _sha_file(
        files_root,
        "notes/system/reviews/g6-r1.md",
        _REROUTE_RATIFY + "legacy-r1\n",
    )
    operator_uri, operator_sha = _sha_file(
        files_root,
        "notes/system/reviews/g6-other-route.md",
        _REROUTE_RATIFY + "other\n",
    )
    tip = _gated_tip(
        g4_stops="",
        g6_stops="cdp_fail_route=nested-grok",
        sidecars=(
            _sidecar("R1", r1_uri, r1_sha)
            + _sidecar("G6-REVIEW-operator", operator_uri, operator_sha)
        ),
    )
    witnesses = row_witnesses(
        "reroute-verdict-no-fallback",
        tip_body=tip,
        deps=_reroute_deps(tmp_path),
        files_root=files_root,
    )
    assert witnesses.get("G6") is None


def test_g4_stamped_route_ignores_clearing_commentary(tmp_path: Path) -> None:
    """G4 with a route stamp does not take a clearing plain G4 body."""
    files_root = tmp_path / "cortex"
    clear_uri, clear_sha = _sha_file(
        files_root, "notes/system/reviews/g4-clear.md", _G4_CLEAR
    )
    hold_uri, hold_sha = _sha_file(
        files_root, "notes/system/reviews/g4-hold.md", _G4_WITHHOLD
    )
    tip = _gated_tip(
        g4_stops="cdp_fail_route=nested-grok",
        g6_stops="",
        sidecars=(
            _sidecar("G4", clear_uri, clear_sha)
            + _sidecar("G4-REVIEW-nested-grok", hold_uri, hold_sha)
        ),
    )
    witnesses = row_witnesses(
        "reroute-verdict-g4",
        tip_body=tip,
        deps=_reroute_deps(tmp_path),
        files_root=files_root,
    )
    assert witnesses.get("G4") is None

    clear_routed_uri, clear_routed_sha = _sha_file(
        files_root, "notes/system/reviews/g4-routed-clear.md", _G4_CLEAR + "routed\n"
    )
    governed = _gated_tip(
        g4_stops="cdp_fail_route=nested-grok",
        g6_stops="",
        sidecars=(
            _sidecar("G4", clear_uri, clear_sha)
            + _sidecar("G4-REVIEW-operator", clear_uri, clear_sha)
            + _sidecar("G4-REVIEW-nested-grok", clear_routed_uri, clear_routed_sha)
        ),
    )
    governed_witnesses = row_witnesses(
        "reroute-verdict-g4-govern",
        tip_body=governed,
        deps=_reroute_deps(tmp_path),
        files_root=files_root,
    )
    assert governed_witnesses["G4"] is not None
    assert governed_witnesses["G4"].detail == clear_routed_uri
    assert (
        governed_witnesses["G4"].source
        == "witness:BIND:G4-REVIEW-nested-grok:route=nested-grok"
    )


def test_run_to_completion_names_route_on_artifact_and_stops() -> None:
    """U8 map: Run to completion → reference-run-to-completion.md."""
    skill = (
        Path(__file__).resolve().parents[2]
        / "cursor-plugins/ulg-ecosystem/skills/conductor/reference-run-to-completion.md"
    )
    text = skill.read_text(encoding="utf-8")
    assert "G6-REVIEW-nested-grok" in text
    assert "G6-REVIEW-operator" in text
    assert "G4-REVIEW-nested-grok" in text
    assert "cdp_fail_route=<route>" in text
    assert "witness:BIND:" in text


def test_sqlite_open_failure_surfaces_in_fold_and_closeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dispatch HOME cannot open cortex sqlite (a:38306).

    ``cortex_conn`` raising ``unable to open database file`` must not leave
    G7 OPEN. Reads go through the cortex API; an API failure is FOLD_FAILED
    on the fold and ``WitnessCortexUnavailable`` from closeout.
    """
    import sqlite3

    from implement_admission.conductor_witness_defaults import (
        DefaultWitnessCortex,
        WitnessCortexUnavailable,
        closeout_witnesses_for_slug,
    )

    def boom(*_args: object, **_kwargs: object) -> None:
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr("cortex_store.db.cortex_conn", boom)

    class _ApiDown:
        def __enter__(self) -> _ApiDown:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def post(self, *_args: object, **_kwargs: object) -> None:
            raise OSError("cortex api unreachable")

    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_args, **_kwargs: _ApiDown(),
    )

    files_root = tmp_path / "cortex"
    scoreboards = files_root / "notes/system/scoreboards"
    scoreboards.mkdir(parents=True)
    tip = (
        "# Scoreboard\n\n## Gated deliverables\n\n"
        "| ID | Deliverable | Status | Stops |\n|---|---|---|---|\n"
        "| G1 | frame | OPEN | |\n"
        "| G7 | land | OPEN | |\n"
    )
    (scoreboards / f"{_SLUG}-scoreboard.md").write_text(tip, encoding="utf-8")
    deps = FoldDeps(
        cortex=DefaultWitnessCortex(),
        git=_StubGit(),
        source_ref=_SOURCE_REF,
        repo=tmp_path / "repo",
    )
    fold = fold_scoreboard(_SLUG, deps=deps, files_root=files_root, write_journal=False)
    assert fold is not None
    assert fold.row_status["G7"] == "FOLD_FAILED"
    assert "cortex api unreachable" in fold.missing_witnesses["G7"]
    assert "| G7 | land | FOLD_FAILED | |" in fold.folded_body
    with pytest.raises(WitnessCortexUnavailable, match="cortex api unreachable"):
        closeout_witnesses_for_slug(
            _SLUG, tip_body=tip, deps=deps, files_root=files_root
        )
