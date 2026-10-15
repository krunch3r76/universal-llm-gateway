"""Tests for continuity messages RAG chunker."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from services.rag.chunkers import chunk_file
from services.rag.chunkers.messages_chunker import (
    CASE_CONTENT_MARKERS,
    MessagesChunkerConfig,
    chunk_messages,
    exchange_matches_case_markers,
    scrub_chunk_text,
    transcript_lane_excluded,
)
from services.rag.config._parsing import _parse_scopes

pytestmark = pytest.mark.offline


def _write_envelope(path: Path, messages: list[dict[str, object]]) -> None:
    payload = {
        "schema": "ulg.continuity.messages/1",
        "messages": messages,
        "meta": {
            "surface": "cursor",
            "session_id": path.stem.replace(".messages", ""),
            "messages_sha256": "abc123",
            "turn_count": len(messages),
            "message_count": len(messages),
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_chunk_file_routes_messages_json_before_suffix(tmp_path: Path) -> None:
    path = tmp_path / "sess.messages.json"
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "<user_query>hello world</user_query>",
                "turn_index": 1,
            },
            {"role": "assistant", "content": "hi back", "turn_index": 1},
        ],
    )
    chunks = chunk_file(path)
    assert len(chunks) == 1
    assert "hello world" in chunks[0].text
    assert chunks[0].metadata["chunker_version"]


def test_synthetic_user_and_tool_lines_dropped(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "[GROK_BOT_HIDDEN_PROMPT] boot",
                "turn_index": 1,
            },
            {
                "role": "user",
                "content": "<user_query>real ask</user_query>",
                "turn_index": 2,
            },
            {
                "role": "assistant",
                "content": "answer\n\n[tool call: Grep]\n\nmore answer",
                "turn_index": 2,
            },
        ],
    )
    chunks = chunk_messages(path)
    assert len(chunks) == 1
    assert "real ask" in chunks[0].text
    assert "tool call" not in chunks[0].text.casefold()
    assert "[tool" not in chunks[0].text


def test_lane_exclusion(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "x", "turn_index": 1},
            {"role": "assistant", "content": "y", "turn_index": 1},
        ],
    )

    def _resolver(_env, _path):  # noqa: ANN001
        return "16934", ()

    cfg = MessagesChunkerConfig(lane_resolver=_resolver)
    assert chunk_messages(path, config=cfg) == []


@pytest.mark.parametrize("marker", CASE_CONTENT_MARKERS)
def test_case_content_marker_excludes_exchange(
    tmp_path: Path, marker: str
) -> None:
    path = tmp_path / "s.messages.json"
    display = {
        "flintridge": "Flintridge Prep",
        "assessment appeals": "Assessment Appeals",
    }
    sample = display.get(marker, marker)
    _write_envelope(
        path,
        [
            {"role": "user", "content": f"Parcel {sample} appeal", "turn_index": 1},
            {"role": "assistant", "content": "ok", "turn_index": 1},
        ],
    )
    assert chunk_messages(path) == []
    assert exchange_matches_case_markers(f"talk about {sample}")


def test_briefly_inform_synthetic_user_dropped(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "Briefly inform the user about the task result.",
                "turn_index": 1,
            },
            {
                "role": "user",
                "content": "<user_query>keep me</user_query>",
                "turn_index": 2,
            },
            {"role": "assistant", "content": "done", "turn_index": 2},
        ],
    )
    chunks = chunk_messages(path)
    assert len(chunks) == 1
    assert "keep me" in chunks[0].text


def test_scrub_secrets_on_chunk_text_only() -> None:
    raw = "Contact me at kay@example.com token sk-abcdefghijklmnopqrstuvwxyz123456"
    scrubbed = scrub_chunk_text(raw)
    assert "@" not in scrubbed
    assert "sk-" not in scrubbed


def test_conversations_scope_excluded_from_all_union() -> None:
    raw = {
        "scopes": {
            "conversations": {
                "prefixes": ["/data/seals/"],
                "exclude_from_union": True,
            },
            "research": {"prefixes": ["/data/papers/"]},
            "all": {"union": True},
        }
    }
    scopes = _parse_scopes(raw["scopes"])
    assert "/data/seals/" in scopes["conversations"].prefixes
    assert "/data/seals/" not in scopes["all"].prefixes
    assert "/data/papers/" in scopes["all"].prefixes


def test_scope_yaml_example_parses() -> None:
    example = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "conversations_scope.example.yaml"
    )
    raw = yaml.safe_load(example.read_text(encoding="utf-8"))
    scopes = _parse_scopes(raw["scopes"])
    assert "conversations" in scopes
    assert scopes["conversations"].exclude_from_union is True
    seals_prefix = scopes["conversations"].prefixes[0]
    assert seals_prefix.endswith("/seals/")
    assert seals_prefix not in scopes["all"].prefixes


def test_transcript_lane_excluded_by_case_tag() -> None:
    assert transcript_lane_excluded("12286", ("case:aab",))


@pytest.mark.parametrize(
    "lane,tags,expected",
    [
        ("16934", (), True),
        ("10223", ("case:foo",), True),
        ("10223", ("role:root",), False),
    ],
)
def test_transcript_lane_excluded_table(
    lane: str, tags: tuple[str, ...], expected: bool
) -> None:
    assert transcript_lane_excluded(lane, tags) is expected
