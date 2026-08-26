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
from implement_admission.conductor_witness import FoldDeps, fold_scoreboard, row_witnesses

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
    ) -> bool:
        _ = thread_id, after_written_at
        return self._resurface

    def nested_implement_has_commits(self, *, nest_under_dispatch_id: str) -> bool:
        _ = nest_under_dispatch_id
        return False


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
    assert len(records) == 2
    assert len(load_journal(_SLUG, files_root=files_root)) == 2


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
    assert fold.row_status["G6"] == "DONE"
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
