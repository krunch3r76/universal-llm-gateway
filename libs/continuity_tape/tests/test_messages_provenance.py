"""Provenance is optional and absent from the wire when unset."""

from __future__ import annotations

import json

import pytest
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    Provenance,
    envelope_wire_dict,
)

pytestmark = pytest.mark.offline


def _bare() -> ContinuityMessagesEnvelope:
    return ContinuityMessagesEnvelope(
        messages=[{"role": "user", "content": "hi"}],
        meta=EnvelopeMeta(surface="cursor", transcript_id="t"),
    )


def test_unset_provenance_omitted_from_wire() -> None:
    envelope = _bare()
    dumped = envelope.model_dump(mode="json", by_alias=True)
    wire = envelope_wire_dict(envelope)
    assert "provenance" not in dumped["meta"]
    assert "provenance" not in wire["meta"]
    meta_dump = EnvelopeMeta(surface="cursor", transcript_id="t").model_dump(
        mode="json"
    )
    assert "provenance" not in meta_dump
    blob = json.dumps(wire, sort_keys=True).encode()
    assert b'"provenance"' not in blob


def test_provenance_literal_roundtrip() -> None:
    raw: Provenance = "raw"
    envelope = ContinuityMessagesEnvelope(
        messages=[],
        meta=EnvelopeMeta(provenance=raw),
    )
    dumped = envelope.model_dump(mode="json", by_alias=True)
    assert dumped["meta"]["provenance"] == "raw"
    transcribed = EnvelopeMeta(provenance="transcribed")
    assert transcribed.model_dump(mode="json")["provenance"] == "transcribed"
