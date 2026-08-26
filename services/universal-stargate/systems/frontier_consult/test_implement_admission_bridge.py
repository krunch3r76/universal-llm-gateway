"""Regression: StargateCortexReader must satisfy implement_admission CortexReader.

Handoff tests stub their own entity_get, so they cannot catch a missing method on
the production reader class. See tasks/specs/implement-input-schema.md §4.1.
"""

from __future__ import annotations

import inspect
from pathlib import Path

from implement_admission.normalize import CortexReader

from systems.frontier_consult.implement_admission_bridge import _repo_base
from systems.frontier_consult.stargate_cortex_reader import StargateCortexReader


def test_stargate_cortex_reader_exposes_entity_get() -> None:
    assert hasattr(StargateCortexReader, "entity_get")
    assert callable(getattr(StargateCortexReader, "entity_get"))
    assert hasattr(StargateCortexReader, "list_relationships")


def test_stargate_cortex_reader_usable_as_cortex_reader() -> None:
    reader = StargateCortexReader()

    def _accepts(reader: CortexReader) -> None:
        assert callable(reader.entity_get)

    _accepts(reader)


def test_stargate_cortex_reader_entity_get_accepts_entity_id_kw() -> None:
    """entity_get must mirror assertion_state dispatch shape (entity_id first)."""
    sig = inspect.signature(StargateCortexReader.entity_get)
    params = list(sig.parameters)
    assert params[0] == "self"
    assert params[1] == "entity_id"


def test_repo_base_no_double_nest_when_root_already_named_repo(tmp_path: Path) -> None:
    outer = tmp_path / "universal-llm-gateway"
    outer.mkdir()
    nested = outer / "universal-llm-gateway"
    nested.mkdir()
    assert _repo_base(outer) == outer.resolve()
