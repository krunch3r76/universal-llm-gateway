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
    load_journal,
    read_tip,
    scoreboard_tip_uri,
    walk_journal_to_tip,
)


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
