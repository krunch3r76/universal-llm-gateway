"""Snapshot write, skip, prefix-extend, and not_prefix."""

from __future__ import annotations

import json

import pytest
from continuity_tape.handoff import (
    HANDOFF_MAX_BYTES,
    fill_grok_binding,
    handoff_json_size,
    normalize_handoff,
)
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
    options = {"snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json", "house": "h", "agent_id": "abcdefghi"}
    base = [{"role": "user", "content": "hello", "turn_index": 1}]
    first = await run_envelope(options, _source(base), fs)
    assert first["action"] == "write"
    old = await fs.read("notes/system/transcripts/grok/h-abcdefghi.raw.messages.json")
    skip = await run_envelope(options, _source(base), fs)
    assert skip["action"] == "skip"
    assert await fs.read("notes/system/transcripts/grok/h-abcdefghi.raw.messages.json") == old
    extended = base + [{"role": "assistant", "content": "yo", "turn_index": 1}]
    over = await run_envelope(options, _source(extended), fs)
    assert over["action"] == "overwrite"
    assert await fs.read("notes/system/transcripts/grok/h-abcdefghi.raw.messages.json") != old
    saved = await fs.read("notes/system/transcripts/grok/h-abcdefghi.raw.messages.json")
    other = [{"role": "user", "content": "different", "turn_index": 1}]
    refused = await run_envelope(options, _source(other), fs)
    assert refused["refused"]["code"] == "envelope.not_prefix"
    assert await fs.read("notes/system/transcripts/grok/h-abcdefghi.raw.messages.json") == saved
    json.loads(saved)


def _author_only(**overrides: object) -> dict:
    letter = {
        "author": "grok-bot-hm1",
        "mission": "resume the house",
        "state": "open",
        "next_acts": ["read the card"],
        "load_bearing": ["intent is not in the transcript"],
    }
    letter.update(overrides)
    return letter


def _letter_over_cap_after_fill(messages: list[dict]) -> dict:
    digest = seal_messages_sha256(messages)
    for pad in range(1, HANDOFF_MAX_BYTES):
        letter = _author_only(mission="m" * pad)
        normalized, _ = normalize_handoff(letter)
        if handoff_json_size(normalized) >= HANDOFF_MAX_BYTES:
            break
        filled, _ = fill_grok_binding(
            normalized,
            messages=messages,
            messages_sha256=digest,
            house="h",
            n=1,
        )
        if handoff_json_size(normalized) < HANDOFF_MAX_BYTES < handoff_json_size(filled):
            return letter
    raise AssertionError("could not build near-cap letter")


@pytest.mark.asyncio
async def test_handoff_refused_when_over_cap_after_binding_fill(fs) -> None:
    messages = [{"role": "user", "content": "hello", "turn_index": 1}]
    options = {
        "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
        "house": "h",
        "agent_id": "abcdefghi",
        "n": 1,
        "handoff": _letter_over_cap_after_fill(messages),
    }
    out = await run_envelope(options, _source(messages), fs)
    assert out["refused"]["code"] == "admit.handoff_too_large"
    assert "after binding fill" in out["refused"]["message"]
    assert await fs.read(options["snapshot_uri"]) is None


@pytest.mark.asyncio
async def test_handoff_under_cap_after_fill_still_writes(fs) -> None:
    messages = [{"role": "user", "content": "hello", "turn_index": 1}]
    options = {
        "snapshot_uri": "notes/system/transcripts/grok/h-abcdefghi.raw.messages.json",
        "house": "h",
        "agent_id": "abcdefghi",
        "n": 1,
        "handoff": _author_only(),
    }
    out = await run_envelope(options, _source(messages), fs)
    assert out.get("refused") is None
    assert out["action"] == "write"
