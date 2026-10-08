"""Snapshot write, skip, prefix-extend, and not_prefix."""

from __future__ import annotations

import json

import pytest
from continuity_tape.messages import seal_messages_sha256

from .envelope import run_envelope

pytestmark = pytest.mark.offline


def _env(messages: list[dict]) -> dict:
    return {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "messages_sha256": seal_messages_sha256(messages),
            "provenance": "raw",
        },
    }


def _source(messages: list[dict]) -> dict:
    env = _env(messages)
    return {
        "envelope": env,
        "messages_sha256": env["meta"]["messages_sha256"],
        "provenance": "raw",
        "producer": "cursor-jsonl-extractor",
        "coverage": "full",
        "turn_count": 1,
        "surface": "cursor",
    }


@pytest.mark.asyncio
async def test_write_skip_prefix_and_refuse(fs) -> None:
    options = {"snapshot_uri": "notes/snap.json", "house": "h", "agent_id": "abcdefghi"}
    base = [{"role": "user", "content": "hello", "turn_index": 1}]
    first = await run_envelope(options, _source(base), fs)
    assert first["action"] == "write"
    old = await fs.read("notes/snap.json")
    skip = await run_envelope(options, _source(base), fs)
    assert skip["action"] == "skip"
    assert await fs.read("notes/snap.json") == old
    extended = base + [{"role": "assistant", "content": "yo", "turn_index": 1}]
    over = await run_envelope(options, _source(extended), fs)
    assert over["action"] == "overwrite"
    assert await fs.read("notes/snap.json") != old
    saved = await fs.read("notes/snap.json")
    other = [{"role": "user", "content": "different", "turn_index": 1}]
    refused = await run_envelope(options, _source(other), fs)
    assert refused["refused"]["code"] == "envelope.not_prefix"
    assert await fs.read("notes/snap.json") == saved
    json.loads(saved)
