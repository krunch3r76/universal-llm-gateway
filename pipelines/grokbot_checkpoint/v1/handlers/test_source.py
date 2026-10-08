"""source.sha_mismatch and pour refusal codes through the source step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from continuity_tape.extract_jsonl import extract_turns_from_jsonl_bytes
from continuity_tape.messages import envelope_wire_dict

from ._clients import file_sha256
from .source import run_source

pytestmark = pytest.mark.offline

_FIXTURE = Path(__file__).parent / "fixtures" / "cursor_window.jsonl"


def _raw_pour() -> dict:
    env = extract_turns_from_jsonl_bytes(
        _FIXTURE.read_bytes(), transcript_id="cursor-window"
    )
    env.meta.provenance = "raw"
    env.meta.coverage = "full"
    env.meta.sources = [
        {
            "kind": "jsonl",
            "producer": {
                "id": "cursor-jsonl-extractor",
                "kind": "extractor",
                "version": None,
            },
        }
    ]
    return envelope_wire_dict(env)


@pytest.mark.asyncio
async def test_sha_mismatch_and_valid(fs) -> None:
    body = json.dumps(_raw_pour()).encode()
    fs.files["notes/pour.json"] = body
    bad = await run_source(
        {"raw_ref": "notes/pour.json", "raw_sha256": "0" * 64}, fs
    )
    assert bad["refused"]["code"] == "source.sha_mismatch"
    good = await run_source(
        {"raw_ref": "notes/pour.json", "raw_sha256": file_sha256(body)},
        fs,
    )
    assert good["refused"] is None
    assert good["provenance"] == "raw"


@pytest.mark.asyncio
async def test_pour_codes_via_source(fs) -> None:
    pour = _raw_pour()
    pour["meta"]["provenance"] = None
    raw = json.dumps(pour).encode()
    fs.files["notes/pour.json"] = raw
    out = await run_source(
        {"raw_ref": "notes/pour.json", "raw_sha256": file_sha256(raw)},
        fs,
    )
    assert out["refused"]["code"] == "pour.provenance_missing"
    valid = json.dumps(_raw_pour()).encode()
    fs.files["notes/raw.json"] = valid
    empty = await run_source({}, fs)
    assert empty["refused"]["code"] == "source.no_pour"
    mismatch = await run_source(
        {
            "transcribed_ref": "notes/raw.json",
            "transcribed_sha256": file_sha256(valid),
        },
        fs,
    )
    assert mismatch["refused"]["code"] == "source.slot_mismatch"
    fallback = await run_source(
        {
            "transcribed_ref": "notes/raw.json",
            "transcribed_sha256": file_sha256(valid),
            "raw_ref": "notes/raw.json",
            "raw_sha256": file_sha256(valid),
        },
        fs,
    )
    assert fallback["provenance"] == "raw"
    assert fallback["fallback"]["from"] == "transcribed"
