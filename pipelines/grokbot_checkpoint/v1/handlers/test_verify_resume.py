"""verify_resume is read-only and reports per-check pass/fail."""

from __future__ import annotations

import json

import pytest

from ._clients import file_sha256
from .verify_resume import run_verify_resume

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_read_only_checks(fs) -> None:
    card = b"card-bytes"
    snap = json.dumps(
        {"meta": {"messages_sha256": "abc", "provenance": "raw"}}
    ).encode()
    fs.files["notes/card.md"] = card
    fs.files["notes/system/transcripts/grok/h-abcdefghi.raw.messages.json"] = snap
    body = (
        "Window: agent_id=a · turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:abc · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )

    async def get(path, params=None):
        return {
            "turns": [
                {"subject": "CHECKPOINT", "body": body, "turn_number": 2, "from": "bot"},
                {
                    "subject": "RECEIPT",
                    "body": "ok",
                    "turn_number": 5,
                    "from": "successor",
                },
            ]
        }, 200

    writes_before = list(fs.writes)
    out = await run_verify_resume(
        {
            "card_uri": "notes/card.md",
            "expected_card_sha256": file_sha256(card),
            "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
            "snapshot_sha256": file_sha256(snap),
            "verify_lane": "v",
            "successor": "successor",
            "fact_turn": 3,
        },
        fs,
        get=get,
    )
    assert fs.writes == writes_before
    assert out["posts"] == 0
    assert all(out["checks"].values())
