"""Hermetic tests for conductor packet materialization."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from implement_admission.conductor_materialize import (
    conductor_packet_contains_use_line,
    conductor_packet_has_lane_b,
    extract_scoreboard_uri,
    load_conductor_context,
    materialize_conductor,
    render_sparse_scoreboard,
    resolve_entry_gate,
)
from implement_admission.conductor_score_journal import (
    forward_mutate_tip,
    load_journal,
    read_tip,
    scoreboard_tip_uri,
    walk_journal_to_tip,
)
from implement_admission.conductor_summon import resolve_summon_mode


class _StubCortex:
    def __init__(self, attrs: dict[str, Any] | None = None) -> None:
        self._attrs = attrs or {}

    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "name": "Layer conductor unify",
            "attributes": {
                "density_triage": "judgment_required",
                **self._attrs,
            },
        }


def test_resolve_entry_gate_g1_without_derived_from() -> None:
    assert resolve_entry_gate(density_triage="judgment_required", derived_from=None) == "G1"


def test_resolve_entry_gate_g2_with_derived_from() -> None:
    assert (
        resolve_entry_gate(
            density_triage="judgment_required",
            derived_from="document:foo-architecture-consult",
        )
        == "G2"
    )


def test_resolve_entry_gate_g5_mechanical() -> None:
    assert resolve_entry_gate(density_triage="mechanical", derived_from=None) == "G5"


def test_materialize_conductor_packet_shape(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
        files_root=files_root,
    )
    assert mp.packet_sha256
    assert "Use-line" not in mp.text  # guard against typo — real marker below
    assert conductor_packet_contains_use_line(mp.text)
    assert conductor_packet_has_lane_b(mp.text)
    assert "packet_kind: conductor" in mp.text
    assert "work_key: todo:layer-conductor-unify" in mp.text
    frontmatter = mp.text.split("---")[1]
    assert "source_ref:" not in frontmatter
    assert not re.search(r"^todo:\s", frontmatter, re.MULTILINE)
    assert "| G1 |" in mp.text
    assert "| G6 |" in mp.text
    uri = extract_scoreboard_uri(mp.text)
    assert uri == scoreboard_tip_uri("layer-conductor-unify")
    tip = read_tip("layer-conductor-unify", files_root=files_root)
    assert tip is not None
    journal = load_journal("layer-conductor-unify", files_root=files_root)
    assert len(journal) == 1
    assert journal[0]["reason"] == "conductor spawn birth"
    assert walk_journal_to_tip("layer-conductor-unify", files_root=files_root) == tip[1]


def test_sparse_scoreboard_has_stops_column() -> None:
    ctx = load_conductor_context("todo:foo", cortex=_StubCortex())
    body = render_sparse_scoreboard(
        source_ref=ctx.source_ref,
        slug=ctx.slug,
        entry_gate=ctx.entry_gate,
        stop_after=ctx.stop_after,
    )
    assert "| Stops |" in body
    assert "stop_after" not in body or ctx.stop_after is None


def test_sparse_scoreboard_stop_after_when_set() -> None:
    ctx = load_conductor_context(
        "todo:foo",
        cortex=_StubCortex({"stop_after": "G1"}),
    )
    body = render_sparse_scoreboard(
        source_ref=ctx.source_ref,
        slug=ctx.slug,
        entry_gate=ctx.entry_gate,
        stop_after=ctx.stop_after,
    )
    assert "**stop_after:** G1" in body


def test_resolve_summon_mode_explicit_wins() -> None:
    assert (
        resolve_summon_mode(
            explicit="confer-and-finish",
            caller_agent="cursor",
            summon_text="anything",
        )
        == "confer_and_finish"
    )
    assert (
        resolve_summon_mode(
            explicit="attended",
            caller_agent="cursor-auto",
            summon_text="run with it",
        )
        == "attended"
    )


def test_resolve_summon_mode_caller_defaults() -> None:
    assert resolve_summon_mode(caller_agent="cursor") == "attended"
    assert resolve_summon_mode(caller_agent="cursor-ide") == "attended"
    assert resolve_summon_mode(caller_agent="cursor-auto") == "confer_and_finish"
    assert resolve_summon_mode(caller_agent=None) == "confer_and_finish"


def test_resolve_summon_mode_confer_markers_in_text() -> None:
    assert (
        resolve_summon_mode(
            caller_agent="cursor",
            summon_text="please run with it on this",
        )
        == "confer_and_finish"
    )


def test_resolve_summon_mode_invalid_explicit_raises() -> None:
    with pytest.raises(ValueError, match="invalid summon_mode"):
        resolve_summon_mode(explicit="solo")


def test_materialize_conductor_attended_packet_strings(tmp_path: Path) -> None:
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
        caller_agent="cursor",
    )
    assert "summon_mode: attended" in mp.text
    assert (
        "G3→G5 attended: resurface score in the summoning IDE chat"
        in mp.text
    )
    assert (
        "Explicit see-score while attended: ROW_PINNED at G3, no pager"
        in mp.text
    )


def test_materialize_conductor_skips_birth_when_tip_exists(tmp_path: Path) -> None:
    files_root = tmp_path / "cortex"
    slug = "rematerialize-skip-test"
    source_ref = f"todo:{slug}"
    out_dir = tmp_path / "packets"
    materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=out_dir,
        files_root=files_root,
    )
    tip_before = read_tip(slug, files_root=files_root)
    assert tip_before is not None
    g1_done_body = re.sub(
        r"(\|\s*G1\s*\|[^|]*\|)\s*OPEN\b",
        r"\1 DONE",
        tip_before[0],
        count=1,
        flags=re.IGNORECASE,
    )
    forward_mutate_tip(
        slug,
        next_body=g1_done_body,
        seat="conductor",
        dispatch_id="d1",
        reason="G1 harvest",
        rows=("G1",),
        delta="G1 OPEN→DONE",
        files_root=files_root,
    )
    tip_mutated = read_tip(slug, files_root=files_root)
    assert tip_mutated is not None
    journal_before = load_journal(slug, files_root=files_root)
    birth_count_before = sum(
        1 for record in journal_before if record.get("reason") == "conductor spawn birth"
    )
    assert birth_count_before == 1

    materialize_conductor(
        source_ref,
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-rematerialize",
        files_root=files_root,
    )
    tip_after = read_tip(slug, files_root=files_root)
    assert tip_after is not None
    assert tip_after[0] == tip_mutated[0]
    assert tip_after[1] == tip_mutated[1]
    journal_after = load_journal(slug, files_root=files_root)
    birth_count_after = sum(
        1 for record in journal_after if record.get("reason") == "conductor spawn birth"
    )
    assert birth_count_after == 1
    assert len(journal_after) == len(journal_before)


def test_materialize_conductor_default_confer_and_finish_strings(tmp_path: Path) -> None:
    out_dir = tmp_path / "packets"
    mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=out_dir,
    )
    assert "summon_mode: confer_and_finish" in mp.text
    assert (
        "G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal)."
        in mp.text
    )
    assert "Explicit see-score: ROW_PINNED at G3 + ping." in mp.text

    auto_mp = materialize_conductor(
        "todo:layer-conductor-unify",
        cortex=_StubCortex(),
        out_dir=tmp_path / "packets-auto",
        caller_agent="cursor-auto",
    )
    assert (
        "G3→G5 default: in-process CDP score-ratify (do-not-fight / likely-optimal)."
        in auto_mp.text
    )
    assert "Explicit see-score: ROW_PINNED at G3 + ping." in auto_mp.text
