"""Provenance is optional and absent from the wire when unset.

Golden bytes are fixtures/envelope_wire_base_87f3104b.json and
envelope_dump_base_87f3104b.json, produced from git show 87f3104b messages.py
envelope_wire_dict and model_dump (see fixtures/README.md). Not regenerated from HEAD.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from continuity_tape.messages import (
    CaptureRef,
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
    assert b'"capture"' not in blob
    fix = Path(__file__).parent / "fixtures"
    wire_golden = (fix / "envelope_wire_base_87f3104b.json").read_text()
    dump_golden = (fix / "envelope_dump_base_87f3104b.json").read_text()
    assert json.dumps(wire, sort_keys=True) + "\n" == wire_golden
    assert json.dumps(dumped, sort_keys=True) + "\n" == dump_golden


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


def test_capture_set_serializes() -> None:
    envelope = ContinuityMessagesEnvelope(
        messages=[],
        meta=EnvelopeMeta(
            surface="grok",
            capture=CaptureRef(
                kind="dom_harvest",
                ref="workspaces://captures/x",
                sha256="ab" * 32,
                pseudo_byte_exact=True,
            ),
        ),
    )
    dumped = envelope.model_dump(mode="json", by_alias=True)
    assert dumped["meta"]["capture"]["kind"] == "dom_harvest"
    assert dumped["meta"]["capture"]["pseudo_byte_exact"] is True
    wire = envelope_wire_dict(envelope)
    assert wire["meta"]["capture"]["ref"] == "workspaces://captures/x"
