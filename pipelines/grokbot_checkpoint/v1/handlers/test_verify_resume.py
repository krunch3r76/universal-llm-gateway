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
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:abc · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )

    async def get(path, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns" and params and params.get("thread") == "v":
            return {
                "turns": [
                    {
                        "subject": "CHECKPOINT",
                        "body": body,
                        "turn_number": 2,
                        "from": "bot",
                    },
                    {
                        "subject": "RECEIPT",
                        "body": "ok",
                        "turn_number": 5,
                        "from": "successor",
                    },
                ]
            }, 200
        return {"turns": []}, 200

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


@pytest.mark.asyncio
async def test_picks_newest_checkpoint_regardless_of_order(fs) -> None:
    card = b"card-bytes"
    snap = json.dumps(
        {"meta": {"messages_sha256": "abc", "provenance": "raw"}}
    ).encode()
    fs.files["notes/card.md"] = card
    fs.files["notes/system/transcripts/grok/h-abcdefghi.raw.messages.json"] = snap
    newer_body = (
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:abc · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )
    older_body = (
        "Window: transcript_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890 · "
        "turns@cp=1 · coverage=full\n"
        "Harvest: transcript:s · messages_sha256:deadbeef · codec:messages-v1 · "
        "surface:cursor · provenance:raw · producer:cursor-jsonl-extractor\n"
    )

    async def get(path, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns" and params and params.get("thread") == "v":
            return {
                "turns": [
                    {
                        "subject": "CHECKPOINT",
                        "body": newer_body,
                        "turn_number": 10,
                        "from": "bot",
                    },
                    {
                        "subject": "CHECKPOINT",
                        "body": older_body,
                        "turn_number": 5,
                        "from": "bot",
                    },
                ]
            }, 200
        return {"turns": []}, 200

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
    assert out["checks"]["harvest_sha"] is True
