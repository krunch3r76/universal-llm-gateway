"""Card row append, sha mismatch, and cp-n replay."""

from __future__ import annotations

import pytest

from ._clients import file_sha256
from .card import run_card

pytestmark = pytest.mark.offline

_CARD = """# house

## Checkpoint log

| n | when | trigger | expected | envelope | pipeline |
|---|---|---|---|---|---|
"""


@pytest.mark.asyncio
async def test_append_mismatch_and_replay(fs) -> None:
    fs.files["notes/card.md"] = _CARD.encode()
    expected = file_sha256(_CARD.encode())
    options = {
        "card_uri": "notes/card.md",
        "expected_card_sha256": expected,
        "n": 1,
        "mode": "seal",
        "trigger": "hop",
    }
    env = {
        "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
        "sha256": "snapsha",
        "messages_sha256": "msgsha",
        "provenance": "raw",
        "producer": "cursor-jsonl-extractor",
    }
    stale = await run_card(
        {**options, "expected_card_sha256": "f" * 64},
        {},
        env,
        fs,
        execution_id="e0",
        seal={"session_id": "s", "refused": None},
    )
    assert stale["refused"]["code"] == "card.sha_mismatch"
    assert fs.writes == []
    out = await run_card(
        options, {}, env, fs, execution_id="e1", seal={"session_id": "s", "refused": None}
    )
    assert out["refused"] is None
    body = (await fs.read("notes/card.md")).decode()
    assert body.count("| 1 |") == 1
    assert "provenance:raw" in body
    untouched = await fs.read("notes/card.md")
    replay = await run_card(
        {**options, "expected_card_sha256": "f" * 64, "n": 1},
        {},
        env,
        fs,
        execution_id="e2",
        seal={"session_id": "s", "refused": None},
    )
    assert replay["replayed"] is True
    assert await fs.read("notes/card.md") == untouched
    assert "pipeline e2" not in body
