"""Provenance is optional and absent from the wire when unset."""

from __future__ import annotations

import json

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
    wire_golden = (
        '{"cells": [], "index": [], "messages": [{"content": "hi", "role": "user"}], '
        '"meta": {"budget_bytes": null, "chat_url": null, "checkpoint_turns": null, '
        '"coverage": null, "degraded": null, "door": null, "execution_id": null, '
        '"extras": false, "message_count": 0, "messages_sha256": "", "observed_at": null, '
        '"payload_bytes": null, "request": null, "sealed_at": null, '
        '"segment_codec_counts": null, "session_id": null, "source_sha256": null, '
        '"sources": null, "streaming": null, "surface": "cursor", "surfaces": null, '
        '"tools": "none", "tools_available": false, "transcript_id": "t", '
        '"truncated": false, "turn_count": 0}, "schema": "ulg.continuity.messages/1"}'
    )
    dump_golden = wire_golden.replace(
        ', "schema":', ', "open_line": null, "schema":'
    )
    assert json.dumps(wire, sort_keys=True) == wire_golden
    assert json.dumps(dumped, sort_keys=True) == dump_golden


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
