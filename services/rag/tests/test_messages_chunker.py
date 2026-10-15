"""Tests for continuity messages RAG chunker."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
import yaml

from services.rag.chunkers import chunk_file
from services.rag.chunkers.messages_chunker import (
    CASE_CONTENT_MARKERS,
    MessagesChunkerConfig,
    chunk_messages,
    exchange_matches_case_markers,
    indexing_source_hash,
    scrub_chunk_text,
    transcript_lane_excluded,
)
from services.rag.config._parsing import _parse_scopes, _parse_watch_directories
from services.rag.watcher_manager.protocols import path_matches_extensions

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


@pytest.mark.parametrize(
    "sample,token",
    [
        (
            "Here is a key:\n-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n",
            "BEGIN OPENSSH",
        ),
        ("token ghp_abcdefghijklmnopqrstuvwxyz1234567890", "ghp_"),
        ("Bearer abcdefghijklmnopqrstuvwxyz1234567890", "Bearer"),
    ],
)
def test_scrub_redacts_secrets(sample: str, token: str) -> None:
    scrubbed = scrub_chunk_text(sample)
    assert token not in scrubbed


def test_scrub_street_and_env() -> None:
    text = "Meet at 1234 North Foothill Blvd\nexport DATABASE_URL=postgres://x"
    scrubbed = scrub_chunk_text(text)
    assert "Foothill" not in scrubbed
    assert "[REDACTED_ENV]" in scrubbed


def test_chunk_ts_from_inline_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "<timestamp>Monday, Jan 1, 2025</timestamp><user_query>hi</user_query>",
                "turn_index": 1,
            },
            {"role": "assistant", "content": "yo", "turn_index": 1},
        ],
    )
    chunks = chunk_messages(path)
    assert chunks[0].metadata.get("ts") == "Monday, Jan 1, 2025"


def test_indexing_source_hash_includes_chunker_version(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    path.write_bytes(b"{}")
    raw_bytes = path.read_bytes()
    h1 = indexing_source_hash(raw_bytes, path)
    h2 = hashlib.sha256(raw_bytes).hexdigest()
    assert h1 != h2


def test_nested_tool_json_stripped(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    blob = '{"type": "tool_use", "tool_use_id": "t1", "input": {"path": "x"}}'
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>q</user_query>", "turn_index": 1},
            {"role": "assistant", "content": f"ok\n\n{blob}", "turn_index": 1},
        ],
    )
    chunks = chunk_messages(path)
    assert "tool_use_id" not in chunks[0].text


def test_case_marker_in_assistant_excludes(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>generic</user_query>", "turn_index": 1},
            {"role": "assistant", "content": "Parcel 25.7489 details", "turn_index": 1},
        ],
    )
    assert chunk_messages(path) == []


def test_b13_style_embedded_synthetic_not_dropped(tmp_path: Path) -> None:
    path = tmp_path / "cursor-2026-10-14-034000-b13.messages.json"
    pasted = (
        "prior chat <user_query>Briefly inform the user about the task result.</user_query> "
        "more paste"
    )
    real = "I gave claude.ai the maestro name because it can make executive decisions"
    content = f"{pasted}<user_query>{real}</user_query>"
    _write_envelope(
        path,
        [
            {"role": "user", "content": content, "turn_index": 1},
            {"role": "assistant", "content": "ack", "turn_index": 1},
        ],
    )
    chunks = chunk_messages(path)
    assert len(chunks) == 1
    assert "maestro name" in chunks[0].text


def test_liaison_hop_synthetic_dropped(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "You are the attended liaison for house 12557 — hop now.",
                "turn_index": 1,
            },
            {
                "role": "user",
                "content": "<user_query>real</user_query>",
                "turn_index": 2,
            },
            {"role": "assistant", "content": "ok", "turn_index": 2},
        ],
    )
    chunks = chunk_messages(path)
    assert len(chunks) == 1
    assert "real" in chunks[0].text
    assert "liaison for house" not in chunks[0].text


def test_agent_bus_thread_tags_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.rag.chunkers.messages_chunker import _load_thread_tags

    bus_db = tmp_path / "agent_bus.db"
    conn = sqlite3.connect(bus_db)
    conn.execute("CREATE TABLE threads (id TEXT PRIMARY KEY, tags TEXT)")
    conn.execute(
        "INSERT INTO threads (id, tags) VALUES (?, ?)",
        ("16934", json.dumps(["case:aab"])),
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(bus_db))
    assert _load_thread_tags("16934") == ["case:aab"]


def test_dominant_lane_16934_excluded_via_resolver(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>x</user_query>", "turn_index": 1},
            {"role": "assistant", "content": "y", "turn_index": 1},
        ],
    )

    def _resolver(_env, _path):  # noqa: ANN001
        return "16934", ()

    cfg = MessagesChunkerConfig(lane_resolver=_resolver)
    assert chunk_messages(path, config=cfg) == []


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
    assert scopes["session_transcripts"].exclude_from_union is True
    seals_prefix = scopes["conversations"].prefixes[0]
    assert seals_prefix.endswith("/seals/")
    assert seals_prefix not in scopes["all"].prefixes
    transcripts_prefix = scopes["session_transcripts"].prefixes[0]
    assert transcripts_prefix not in scopes["all"].prefixes


def test_watch_directories_match_messages_json(tmp_path: Path) -> None:
    example = (
        Path(__file__).resolve().parents[1]
        / "config"
        / "conversations_scope.example.yaml"
    )
    raw = yaml.safe_load(example.read_text(encoding="utf-8"))
    watchers = _parse_watch_directories(raw["watch_directories"])
    assert watchers
    wd = watchers[0]
    assert wd.recursive is True
    exts = tuple(wd.extensions)
    msg = tmp_path / "cursor-2026-09-22-082500-cdf.messages.json"
    msg.write_text("{}", encoding="utf-8")
    lock = tmp_path / ".lock.cursor-2026-09-22-082500-cdf.messages.json"
    lock.write_text("{}", encoding="utf-8")
    source = tmp_path / "cursor-2026-09-22-082500-cdf.source.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    assert path_matches_extensions(msg, exts)
    assert not path_matches_extensions(source, exts)
    assert path_matches_extensions(lock, exts)


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
