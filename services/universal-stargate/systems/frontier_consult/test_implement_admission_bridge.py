"""Regression: StargateCortexReader must satisfy implement_admission CortexReader.

Handoff tests stub their own entity_get, so they cannot catch a missing method on
the production reader class. See tasks/specs/implement-input-schema.md §4.1.
"""

from __future__ import annotations

import inspect

from implement_admission.normalize import CortexReader

from systems.frontier_consult.implement_admission_bridge import StargateCortexReader


def test_stargate_cortex_reader_exposes_entity_get() -> None:
    assert hasattr(StargateCortexReader, "entity_get")
    assert callable(getattr(StargateCortexReader, "entity_get"))


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
