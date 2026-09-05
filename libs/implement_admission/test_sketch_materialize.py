"""Tests for sketch materializer (contract=sketch)."""

from __future__ import annotations

from pathlib import Path

import pytest

from implement_admission.sketch_materialize import (
    materialize_sketch,
    sketch_packet_has_r1_parts,
)


class _FakeCortex:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "name": "Test todo",
            "description": "Flatten contract enum",
            "attributes": {},
        }


def test_sketch_materialize_emits_r1_parts(tmp_path: Path) -> None:
    mp = materialize_sketch(
        "todo:sketch-admit-contract-flatten",
        cortex=_FakeCortex(),
        out_dir=tmp_path,
    )
    assert sketch_packet_has_r1_parts(mp.text)
    assert "contract: sketch" in mp.text
    assert "scope_pin" in mp.text
    assert "negative_space" in mp.text
    assert "output_envelope" in mp.text
    assert "transfer_predicate" in mp.text
    assert "packet_kind" not in mp.text


def test_sketch_requires_todo_ref() -> None:
    with pytest.raises(ValueError, match="todo:"):
        materialize_sketch(
            "plan:foo",
            cortex=_FakeCortex(),
            out_dir=Path("/tmp/unused"),
        )
