"""One FACT per (house, n)."""

from __future__ import annotations

import pytest

from .fact import run_fact

pytestmark = pytest.mark.offline


@pytest.mark.asyncio
async def test_fact_once() -> None:
    sent: list[dict] = []
    store: list[dict] = []

    async def get(path: str, params=None):
        if path.startswith("/threads/") and path.endswith("/turns"):
            return {"detail": "Not Found"}, 404
        if path == "/turns" and params and params.get("thread") == "v":
            return {"turns": list(store)}, 200
        return {"turns": []}, 200

    async def send(**kwargs):
        sent.append(kwargs)
        store.append(
            {
                "subject": kwargs["subject"],
                "body": kwargs["body"],
                "turn_number": 7,
                "superseded": False,
            }
        )
        return {"turn_number": 7}, 200

    options = {
        "house": "h",
        "n": 1,
        "verify_lane": "v",
        "bus_identity": "bot",
        "verifier": "verifier",
        "trigger": "hop",
        "card_uri": "notes/card.md",
    }
    card = {"uri": "notes/card.md", "written_sha256": "ccc"}
    env = {
        "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
        "sha256": "sss",
        "provenance": "raw",
        "producer": "cursor-jsonl-extractor",
    }
    first = await run_fact(options, card, env, get=get, send=send)
    second = await run_fact(options, card, env, get=get, send=send)
    assert first["skipped"] is False
    assert second["skipped"] is True
    assert len(sent) == 1
    assert "provenance raw" in sent[0]["body"]
    assert "capture none" in sent[0]["body"]
    assert "producer cursor-jsonl-extractor" in sent[0]["body"]
