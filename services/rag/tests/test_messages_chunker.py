"""Tests for continuity messages RAG chunker."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from services.rag.chunkers import chunk_file
from services.rag.chunkers._sizing import _CHUNK_CHARS_PAD, _CHUNK_CHARS_TARGET
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

_PROSE_HARD_MAX = _CHUNK_CHARS_TARGET + _CHUNK_CHARS_PAD
_MAP_SLOT_TOKENS = 4096
_PREFLIGHT_OVERHEAD = 287
_EFFECTIVE_CTX_PER_SLOT = 8192

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _hermetic_messages_chunker(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.rag.chunkers.messages_chunker as mc

    mc._THREAD_TAGS_CACHE.clear()
    mc._THREAD_TAGS_BUS_WARNED.clear()
    mc._LANE_RESOLVER_LOGGED = False

    class _FakeConn:
        def execute(self, _q: str, _params: tuple[str, ...]) -> object:
            class _Row:
                def fetchone(self) -> None:
                    return None

            return _Row()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.schema_snapshot.open_live_db_readonly",
        lambda: _FakeConn(),
    )
    monkeypatch.setattr(
        "cortex_store.transcript_projection_membership.fetch_thread_detail",
        lambda _tid: {"tags": []},
    )


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


def _chunk_fits_map_slot(text: str) -> bool:
    est = len(text) // 4 + _PREFLIGHT_OVERHEAD + _MAP_SLOT_TOKENS
    return len(text) <= _PROSE_HARD_MAX and est <= _EFFECTIVE_CTX_PER_SLOT


def _exact_paragraph(n: int, *, word: str = "word") -> str:
    """Build a paragraph of exactly ``n`` characters, spaced unless ``word`` has no break."""
    unit = word + " "
    text = (unit * ((n // len(unit)) + 2))[:n]
    if len(text) < n:
        text += "x" * (n - len(text))
    return text[:n]


def _assert_chunks_fit(chunks: list, *, label: str) -> None:
    assert chunks, label
    for chunk in chunks:
        assert _chunk_fits_map_slot(chunk.text), (
            f"{label} len={len(chunk.text)} "
            f"est={len(chunk.text) // 4 + _PREFLIGHT_OVERHEAD + _MAP_SLOT_TOKENS}"
        )


def test_oversized_exchange_splits_under_prose_budget(tmp_path: Path) -> None:
    """b13/d9e shape plus review probes that overlap and spaceless splits used to exceed."""
    path = tmp_path / "cursor-2026-10-14-034000-b13.messages.json"
    assistant_body = ("paragraph content. " * 400 + "\n\n") * 30
    assistant_body = assistant_body[:85000]
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "<user_query>summarize the thread</user_query>",
                "turn_index": 1,
            },
            {"role": "assistant", "content": assistant_body, "turn_index": 1},
        ],
    )
    chunks = chunk_messages(path)
    assert len(chunks) > 1
    _assert_chunks_fit(chunks, label="b13")
    assert all(c.metadata["turn_index"] == 1 for c in chunks)

    probes: list[tuple[str, str]] = [
        ("four-5100", "\n\n".join(_exact_paragraph(5100) for _ in range(4))),
        ("spaceless-20000", "A" * 20000),
        ("four-3000", "\n\n".join(_exact_paragraph(3000) for _ in range(4))),
    ]
    for label, assistant in probes:
        probe_path = tmp_path / f"{label}.messages.json"
        _write_envelope(
            probe_path,
            [
                {
                    "role": "user",
                    "content": "<user_query>summarize the thread</user_query>",
                    "turn_index": 1,
                },
                {"role": "assistant", "content": assistant, "turn_index": 1},
            ],
        )
        probe_chunks = chunk_messages(probe_path)
        assert len(probe_chunks) > 1, label
        _assert_chunks_fit(probe_chunks, label=label)
        assert assistant[:24] in "".join(c.text for c in probe_chunks)
        assert assistant[-24:] in "".join(c.text for c in probe_chunks)


def test_short_exchange_stays_one_chunk(tmp_path: Path) -> None:
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {
                "role": "user",
                "content": "<user_query>short question</user_query>",
                "turn_index": 1,
            },
            {"role": "assistant", "content": "brief answer", "turn_index": 1},
        ],
    )
    chunks = chunk_messages(path)
    assert len(chunks) == 1
    assert "short question" in chunks[0].text
    assert chunks[0].metadata.get("chunk_index") == 0

    fitting = tmp_path / "fit-2000-2500.messages.json"
    assistant = _exact_paragraph(2000) + "\n\n" + _exact_paragraph(2500)
    _write_envelope(
        fitting,
        [
            {
                "role": "user",
                "content": "<user_query>short question</user_query>",
                "turn_index": 1,
            },
            {"role": "assistant", "content": assistant, "turn_index": 1},
        ],
    )
    fitting_chunks = chunk_messages(fitting)
    assert len(fitting_chunks) == 1
    assert _chunk_fits_map_slot(fitting_chunks[0].text)
    assert assistant[:24] in fitting_chunks[0].text
    assert assistant[-24:] in fitting_chunks[0].text


def test_markdown_chunk_does_not_use_messages_chunker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.rag.chunkers.office_dispatch as od

    calls: list[Path] = []

    def _boom(path: str | Path, **kwargs: object) -> list:
        calls.append(Path(path))
        raise AssertionError("chunk_messages must not run for markdown")

    monkeypatch.setattr(od, "chunk_messages", _boom)
    md = tmp_path / "note.md"
    md.write_text("# Title\n\nSome prose for indexing.\n", encoding="utf-8")
    chunks = chunk_file(md)
    assert not calls
    assert chunks
    assert "Title" in chunks[0].text


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
def test_case_content_marker_excludes_exchange(tmp_path: Path, marker: str) -> None:
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
            {
                "role": "user",
                "content": "<user_query>generic</user_query>",
                "turn_index": 1,
            },
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


def test_agent_bus_thread_tags_via_http(monkeypatch: pytest.MonkeyPatch) -> None:
    import services.rag.chunkers.messages_chunker as mc

    calls: list[str] = []

    def _fake_detail(tid: str) -> dict[str, object]:
        calls.append(tid)
        return {"id": tid, "tags": ["case:aab"]}

    monkeypatch.setattr(
        "cortex_store.transcript_projection_membership.fetch_thread_detail",
        _fake_detail,
    )
    assert mc._load_thread_tags("16934") == ["case:aab"]
    assert mc._load_thread_tags("16934") == ["case:aab"]
    assert calls == ["16934"]


def test_thread_tags_retry_after_failed_lookup(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import services.rag.chunkers.messages_chunker as mc

    calls = 0

    def _flaky_detail(tid: str) -> dict[str, object] | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            return None
        return {"id": tid, "tags": ["case:retry"]}

    monkeypatch.setattr(
        "cortex_store.transcript_projection_membership.fetch_thread_detail",
        _flaky_detail,
    )
    with caplog.at_level(logging.WARNING):
        assert mc._load_thread_tags("10223") == []
        assert mc._load_thread_tags("10223") == ["case:retry"]
    assert calls == 2
    assert sum(1 for r in caplog.records if "thread tags (agent-bus)" in r.message) == 1


def test_load_thread_tags_logs_once_when_fetch_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import services.rag.chunkers.messages_chunker as mc

    def _boom(_tid: str) -> dict[str, object]:
        raise ConnectionError("bus down")

    monkeypatch.setattr(
        "cortex_store.transcript_projection_membership.fetch_thread_detail",
        _boom,
    )
    with caplog.at_level(logging.WARNING):
        assert mc._load_thread_tags("10223") == []
        assert mc._load_thread_tags("10223") == []
    assert sum(1 for r in caplog.records if "thread tags (agent-bus)" in r.message) == 1


def test_default_lane_resolver_excludes_case_tag_from_bus(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.rag.chunkers.messages_chunker as mc

    class _FakeConn:
        def execute(self, _q: str, _params: tuple[str, ...]) -> object:
            class _Row:
                def fetchone(self) -> tuple[str]:
                    return ("10223",)

            return _Row()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.schema_snapshot.open_live_db_readonly",
        lambda: _FakeConn(),
    )
    monkeypatch.setattr(
        "cortex_store.transcript_projection_membership.fetch_thread_detail",
        lambda _tid: {"tags": ["case:x"]},
    )
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>x</user_query>", "turn_index": 1},
            {"role": "assistant", "content": "y", "turn_index": 1},
        ],
    )
    cfg = MessagesChunkerConfig(lane_resolver=mc.default_lane_resolver)
    assert chunk_messages(path, config=cfg) == []


def test_lane_resolver_sqlite_error_warns_once_without_exc_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging
    import sqlite3

    def _op_error() -> object:
        raise sqlite3.OperationalError("no such column: dominant_lane")

    monkeypatch.setattr(
        "cortex_store.schema_snapshot.open_live_db_readonly",
        _op_error,
    )
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>x</user_query>", "turn_index": 1},
            {"role": "assistant", "content": "y", "turn_index": 1},
        ],
    )
    with caplog.at_level(logging.WARNING):
        assert len(chunk_messages(path, config=MessagesChunkerConfig())) == 1
        assert len(chunk_messages(path, config=MessagesChunkerConfig())) == 1
    warnings = [r for r in caplog.records if "lane resolver (cortex)" in r.message]
    assert len(warnings) == 1
    assert all(r.exc_info is None for r in warnings)


def test_lane_resolver_programming_error_logs_exc_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    import services.rag.chunkers.messages_chunker as mc

    mc._LANE_RESOLVER_LOGGED = False

    def _type_error() -> object:
        raise TypeError("bad cortex row shape")

    monkeypatch.setattr(
        "cortex_store.schema_snapshot.open_live_db_readonly",
        _type_error,
    )
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>x</user_query>", "turn_index": 1},
            {"role": "assistant", "content": "y", "turn_index": 1},
        ],
    )
    with caplog.at_level(logging.ERROR):
        chunks = chunk_messages(path, config=MessagesChunkerConfig())
    assert len(chunks) == 1
    exc_records = [r for r in caplog.records if r.exc_info is not None]
    assert exc_records
    assert any("lane resolver (cortex)" in r.message for r in exc_records)


def test_chunk_yields_empty_tags_when_bus_endpoint_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import services.rag.chunkers.messages_chunker as mc

    mc._THREAD_TAGS_CACHE.clear()
    mc._THREAD_TAGS_BUS_WARNED.clear()

    class _FakeConn:
        def execute(self, _q: str, _params: tuple[str, ...]) -> object:
            class _Row:
                def fetchone(self) -> tuple[str]:
                    return ("10223",)

            return _Row()

        def close(self) -> None:
            return None

    monkeypatch.setattr(
        "cortex_store.schema_snapshot.open_live_db_readonly",
        lambda: _FakeConn(),
    )
    monkeypatch.setattr(
        "cortex_store.transcript_projection_membership.fetch_thread_detail",
        lambda _tid: None,
    )
    path = tmp_path / "s.messages.json"
    _write_envelope(
        path,
        [
            {"role": "user", "content": "<user_query>x</user_query>", "turn_index": 1},
            {"role": "assistant", "content": "y", "turn_index": 1},
        ],
    )
    cfg = MessagesChunkerConfig(lane_resolver=mc.default_lane_resolver)
    chunks = chunk_messages(path, config=cfg)
    assert len(chunks) == 1


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


def test_collect_directory_candidates_messages_json_and_lock_skip(
    tmp_path: Path,
) -> None:
    from services.rag.directory_ops import collect_directory_candidates

    msg = tmp_path / "cursor-2026-09-22-082500-cdf.messages.json"
    msg.write_text("{}", encoding="utf-8")
    lock = tmp_path / ".lock.cursor-2026-09-22-082500-cdf.messages.json"
    lock.write_bytes(b"")
    exts = {"json", "messages.json"}
    paths, walked = collect_directory_candidates(
        dir_path=tmp_path, extensions=exts, collect_walked_sources=True
    )
    assert paths == [msg]
    assert walked == {str(msg.resolve())}


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
