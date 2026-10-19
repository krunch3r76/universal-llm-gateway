"""Projected messages JSON discovery, skips, and the hard chunk cap."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from services.rag.chunkers import chunk_file
from services.rag.chunkers._sizing import CHUNK_HARD_CAP_BYTES
from services.rag.chunkers.messages_chunker import indexing_source_hash
from services.rag.chunkers.paragraph_utils import _split_paragraphs, _word_split
from services.rag.chunkers.projected_messages import (
    SNIFF_BYTES,
    UseSealChunker,
    chunk_json_file,
    log_skip,
    reset_skip_log,
)

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear_skip_log() -> None:
    reset_skip_log()


def _cap(text: str) -> int:
    return len(text.encode("utf-8"))


def _assert_capped(chunks: list) -> None:
    assert chunks
    for chunk in chunks:
        assert _cap(chunk.text) <= CHUNK_HARD_CAP_BYTES


def test_per_source_fixtures(tmp_path: Path) -> None:
    cursor = tmp_path / "cursor.json"
    cursor.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "<user_query>hello cursor</user_query>",
                    },
                    {"role": "assistant", "content": "cursor reply"},
                ]
            }
        ),
        encoding="utf-8",
    )
    claude = tmp_path / "claude.json"
    claude.write_text(
        json.dumps(
            [
                {"role": "user", "content": "hello claude"},
                {"role": "assistant", "content": "claude reply"},
            ]
        ),
        encoding="utf-8",
    )
    grok = tmp_path / "grok.json"
    grok.write_text(
        json.dumps(
            {
                "meta": {"observed_at": "2026-10-18"},
                "messages": [
                    {"role": "user", "content": "hello grok"},
                    {"role": "assistant", "content": "grok reply"},
                ],
            }
        ),
        encoding="utf-8",
    )
    for path, needle in (
        (cursor, "hello cursor"),
        (claude, "hello claude"),
        (grok, "hello grok"),
    ):
        chunks = chunk_file(path)
        _assert_capped(chunks)
        assert needle in chunks[0].text


def test_tape_raw_and_render_skip(tmp_path: Path) -> None:
    tape = tmp_path / "tape.json"
    tape.write_text(
        json.dumps({"cells": [{"cp_ordinal": 1}], "index": [], "open_line": {}}),
        encoding="utf-8",
    )
    raw = tmp_path / "snap.raw.json"
    raw.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ]
            }
        ),
        encoding="utf-8",
    )
    unsealed = tmp_path / "unsealed.json"
    unsealed.write_text(
        json.dumps(
            {
                "meta": {"provenance": "raw"},
                "messages": [
                    {"role": "user", "content": "u"},
                    {"role": "assistant", "content": "a"},
                ],
            }
        ),
        encoding="utf-8",
    )
    render = tmp_path / "notes" / "system" / "transcripts" / "session-close.md"
    render.parent.mkdir(parents=True)
    render.write_text("Session closed.\n\nNo turn headings here.\n", encoding="utf-8")
    fine = tmp_path / "fine.json"
    fine.write_text(
        json.dumps([{"messages": [{"role": "user", "content": "q"}]}]),
        encoding="utf-8",
    )
    assert chunk_file(tape) == []
    assert chunk_file(raw) == []
    assert chunk_file(unsealed) == []
    assert chunk_file(render) == []
    assert chunk_file(fine) == []


def test_real_shape_tape_json_skips(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A rendered tape (open_line, cells, messages, index) is not a transcript."""
    path = tmp_path / "tape.json"
    path.write_text(
        json.dumps(
            {
                "open_line": {"thread_id": "15851", "turn_count": 1},
                "cells": [{"cp_ordinal": 1}],
                "messages": [
                    {"role": "user", "content": "needle-real-tape"},
                    {"role": "assistant", "content": "tape reply"},
                ],
                "index": [{"turn": 1}],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level("INFO", logger="services.rag.chunkers.projected_messages"):
        assert chunk_file(path) == []
    assert any(
        rec.message == f"rag_chunk_skip path={path} reason=tape"
        for rec in caplog.records
    )


def _recognized_fence_lengths(text: str) -> tuple[bool, set[int]]:
    """Return whether fences are closed, and the run lengths that opened them.

    A shorter backtick line inside a longer fence is payload, not a fence.
    """
    opener: int | None = None
    lengths: set[int] = set()
    for line in text.splitlines():
        if not line.startswith("`"):
            continue
        run = 0
        for char in line:
            if char != "`":
                break
            run += 1
        if run < 3:
            continue
        info = line[run:]
        if opener is None:
            opener = run
            lengths.add(run)
            continue
        if run >= opener and info.strip() == "":
            opener = None
            lengths.add(run)
    return opener is None, lengths


def test_fake_heading_stays_in_body(tmp_path: Path) -> None:
    path = tmp_path / "quoted.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "first", "turn_index": 1},
                    {
                        "role": "assistant",
                        "content": "body\n\n## Turn 9 - fake - user\n\nstill body",
                        "turn_index": 2,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    assert not any(chunk.text.startswith("## Turn 9") for chunk in chunks)
    assert any("Turn 9 - fake" in chunk.text for chunk in chunks)


def test_quoted_next_turn_heading_stays_in_body(tmp_path: Path) -> None:
    path = tmp_path / "quoted-next.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": "first\n\n## Turn 2 - fake - user\n\nstolen",
                    },
                    {"role": "assistant", "content": "real second"},
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    starts = [chunk.text.splitlines()[0] for chunk in chunks]
    assert not any(start.startswith("## Turn 2 - fake") for start in starts)
    assert any("## Turn 2 - fake - user" in chunk.text for chunk in chunks)
    assert any(
        "stolen" in chunk.text and "real second" not in chunk.text for chunk in chunks
    )
    assert any(
        start.startswith("## Turn 2 - ") and start.endswith("assistant")
        for start in starts
    )
    assert any("real second" in chunk.text for chunk in chunks)


def test_tool_result_long_fence_stays_balanced(tmp_path: Path) -> None:
    payload = "```python\nprint(1)\n```\n" + ("y" * 30_000)
    path = tmp_path / "long-fence.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "run"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_result",
                                "name": "bash",
                                "content": payload,
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    assert len(chunks) > 1
    saw_long = False
    saw_inner = False
    for chunk in chunks:
        balanced, lengths = _recognized_fence_lengths(chunk.text)
        assert balanced, chunk.text[:120]
        assert len(lengths) <= 1
        if 4 in lengths:
            saw_long = True
        if "```python" in chunk.text:
            saw_inner = True
    assert saw_long
    assert saw_inner


def test_sealed_raw_messages_json_skips_as_raw_before_seal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Name wins: ``*.raw.messages.json`` is raw even when the body is a seal.

    ``_is_raw_name`` matches a bare ``raw`` dot-segment, including ``raw.json``,
    and runs before ``_is_seal``. A sealed body is not handed to ``chunk_messages``.
    """
    path = tmp_path / "session.raw.messages.json"
    path.write_text(
        json.dumps(
            {
                "schema": "ulg.continuity.messages/1",
                "meta": {"provenance": "raw", "sealed_at": "2026-10-19T00:00:00Z"},
                "messages": [
                    {"role": "user", "content": "sealed-raw"},
                    {"role": "assistant", "content": "still raw by name"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level("INFO", logger="services.rag.chunkers.projected_messages"):
        assert chunk_json_file(path) == []
        assert not isinstance(chunk_json_file(path), UseSealChunker)
    assert any(
        rec.message == f"rag_chunk_skip path={path} reason=raw"
        for rec in caplog.records
    )


def test_longer_backtick_run_closes_shorter_fence(tmp_path: Path) -> None:
    path = tmp_path / "commonmark-close.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "go"},
                    {
                        "role": "assistant",
                        "content": "```python\nkeep-token\n````\noutside-token",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "keep-token" in joined
    assert "outside-token" in joined
    for chunk in chunks:
        if "outside-token" not in chunk.text:
            continue
        assert chunk.text.index("````") < chunk.text.index("outside-token")
        balanced, _lengths = _recognized_fence_lengths(chunk.text)
        assert balanced, chunk.text[:200]


def test_raw_dotted_json_names_skip(tmp_path: Path) -> None:
    payload = json.dumps(
        {
            "messages": [
                {"role": "user", "content": "u"},
                {"role": "assistant", "content": "a"},
            ]
        }
    )
    for name in ("h.raw.messages.json", "x.raw.staging.json"):
        path = tmp_path / name
        path.write_text(payload, encoding="utf-8")
        assert chunk_file(path) == []


def test_two_megabyte_turn_and_tool_result_stay_under_cap(tmp_path: Path) -> None:
    prose = tmp_path / "prose.json"
    prose.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": ("word " * 400_000)},
                ]
            }
        ),
        encoding="utf-8",
    )
    tool = tmp_path / "tool.json"
    tool.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "run"},
                    {
                        "role": "assistant",
                        "content": "```tool\n" + ("x" * 2_000_000) + "\n```",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    prose_chunks = chunk_file(prose)
    tool_chunks = chunk_file(tool)
    _assert_capped(prose_chunks)
    _assert_capped(tool_chunks)
    assert len(prose_chunks) > 1
    assert len(tool_chunks) > 1
    assert any("(part " in chunk.text for chunk in tool_chunks)
    for chunk in tool_chunks:
        assert chunk.text.count("```") % 2 == 0
        assert not _fence_only_piece(chunk.text)


def test_giant_token_is_cut() -> None:
    pieces = _word_split("a" * 20_000, 100)
    assert len(pieces) > 1
    assert all(len(piece.encode("utf-8")) <= 100 for piece in pieces)
    carry = "w " * 3000
    paras = _split_paragraphs(f"{carry}\n\n{carry}\n\n{'z' * 8000}", 1000, 100)
    assert paras
    assert all(len(text.encode("utf-8")) <= CHUNK_HARD_CAP_BYTES for text, _ in paras)


def test_policy_version_changes_hash(tmp_path: Path) -> None:
    transcript = tmp_path / "notes" / "system" / "transcripts" / "a.json"
    transcript.parent.mkdir(parents=True)
    transcript.write_bytes(b"{}")
    raw = transcript.read_bytes()
    hashed = indexing_source_hash(raw, transcript)
    plain = hashlib.sha256(raw).hexdigest()
    assert hashed != plain
    other = tmp_path / "paper.pdf"
    other.write_bytes(raw)
    assert indexing_source_hash(raw, other) == plain


def _pad_content_straddling_sniff(shift: int, data: dict) -> str:
    emoji = "\U0001f600"
    target = SNIFF_BYTES - (shift + 1)
    pad_len = max(0, target - 64)
    for _ in range(64):
        data["messages"][0]["content"] = "a" * pad_len + emoji + "tail"
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        idx = raw.find(emoji.encode())
        if idx == target:
            return data["messages"][0]["content"]
        if idx < 0:
            pad_len += 128
        else:
            pad_len += target - idx
    raise AssertionError("could not place emoji at sniff straddle")


@pytest.mark.parametrize("shift", (0, 1, 2))
def test_sniff_straddle_does_not_drop_transcript(tmp_path: Path, shift: int) -> None:
    data = {
        "messages": [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "reply"},
        ]
    }
    data["messages"][0]["content"] = _pad_content_straddling_sniff(shift, data)
    path = tmp_path / "straddle.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    assert len(raw) > SNIFF_BYTES
    emoji_at = raw.index("\U0001f600".encode())
    assert emoji_at == SNIFF_BYTES - (shift + 1)
    routed = chunk_json_file(path)
    assert routed != []
    assert not isinstance(routed, UseSealChunker)
    assert any("tail" in chunk.text for chunk in routed)


@pytest.mark.parametrize("shift", (0, 1, 2))
def test_sniff_straddle_seal_deferred_to_messages_chunker(
    tmp_path: Path, shift: int
) -> None:
    data = {
        "schema": "ulg.continuity.messages/1",
        "messages": [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "reply"},
        ],
    }
    data["messages"][0]["content"] = _pad_content_straddling_sniff(shift, data)
    path = tmp_path / "session.messages.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    raw = path.read_bytes()
    assert len(raw) > SNIFF_BYTES
    emoji_at = raw.index("\U0001f600".encode())
    assert emoji_at == SNIFF_BYTES - (shift + 1)
    assert isinstance(chunk_json_file(path), UseSealChunker)


def test_four_tick_fence_survives_paragraph_split(tmp_path: Path) -> None:
    inner = ("z" * 16_000) + "\n``` not a fence closer\n"
    body = f"````\n{inner}\n\n```python text"
    path = tmp_path / "four-tick.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "go"},
                    {"role": "assistant", "content": body},
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    assert len(chunks) > 1
    for chunk in chunks:
        balanced, lengths = _recognized_fence_lengths(chunk.text)
        assert balanced, chunk.text[:160]
        assert lengths <= {4}
    assert any("```python text" in chunk.text for chunk in chunks)


def test_tool_result_list_split_keeps_following_prose_outside_fence(
    tmp_path: Path,
) -> None:
    long_block = "y" * 16_000
    path = tmp_path / "tool-list.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "run"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_result",
                                "name": "bash",
                                "content": [
                                    {"type": "text", "text": long_block},
                                    {"type": "text", "text": "second block"},
                                ],
                            },
                            {"type": "text", "text": "follow-up prose"},
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    tool_chunks = [c for c in chunks if long_block[:80] in c.text]
    prose_chunks = [c for c in chunks if "follow-up prose" in c.text]
    assert tool_chunks
    assert prose_chunks
    for chunk in tool_chunks:
        balanced, lengths = _recognized_fence_lengths(chunk.text)
        assert balanced
        assert "follow-up prose" not in chunk.text
    for chunk in prose_chunks:
        balanced, lengths = _recognized_fence_lengths(chunk.text)
        assert balanced
        assert long_block[:80] not in chunk.text


def _fence_only_piece(text: str) -> bool:
    """True when the chunk body is only fence lines, such as `` ```tool\\n``` ``."""
    body = text.split("\n\n", 1)[-1]
    lines = [line for line in body.split("\n") if line.strip()]
    if not lines:
        return False
    return all(line.startswith("```") for line in lines)


def test_python_fence_info_string_drops_empty_reopen(tmp_path: Path) -> None:
    payload = "p" * 20_690
    path = tmp_path / "python-fence.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "run"},
                    {
                        "role": "assistant",
                        "content": f"```python\n{payload}\n```",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    assistant = [chunk for chunk in chunks if "assistant" in chunk.text.splitlines()[0]]
    assert assistant
    for chunk in assistant:
        assert not _fence_only_piece(chunk.text)
        assert "```python\n```" not in chunk.text
    assert any(payload[:80] in chunk.text for chunk in assistant)


def test_envelope_dump_with_messages_is_tape(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fixture = (
        Path(__file__).resolve().parents[3]
        / "libs"
        / "continuity_tape"
        / "tests"
        / "fixtures"
        / "envelope_dump_base_87f3104b.json"
    )
    shape = json.loads(fixture.read_text(encoding="utf-8"))
    shape["messages"] = [
        {"role": "user", "content": "needle-tape"},
        {"role": "assistant", "content": "tape reply"},
    ]
    raw = json.dumps(shape)
    with caplog.at_level("INFO", logger="services.rag.chunkers.projected_messages"):
        for name in ("x.json", "x.tape.json"):
            path = tmp_path / name
            path.write_text(raw, encoding="utf-8")
            assert chunk_file(path) == []
            assert any(
                rec.message == f"rag_chunk_skip path={path} reason=tape"
                for rec in caplog.records
            )


def test_seal_parse_error_raises(tmp_path: Path) -> None:
    invalid = tmp_path / "session.messages.json"
    invalid.write_bytes(b"\xff\xfe")
    with pytest.raises(UnicodeError):
        chunk_json_file(invalid)
    bom = tmp_path / "bom.messages.json"
    bom.write_bytes(b"\xef\xbb\xbf" + b'{"schema": "ulg.continuity.messages/1"}')
    with pytest.raises(json.JSONDecodeError):
        chunk_json_file(bom)
    plain = tmp_path / "broken.json"
    plain.write_bytes(b"\xff\xfe")
    assert chunk_json_file(plain) == []


def test_projected_tool_result_secret_is_scrubbed(tmp_path: Path) -> None:
    path = tmp_path / "secret.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "run"},
                    {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_result",
                                "name": "bash",
                                "content": "token sk-abcdefghijklmnopqrstuvwxyz123456",
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "sk-" not in joined
    assert "[REDACTED_SECRET]" in joined


def test_projected_excluded_lane_skips(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "lane.json"
    path.write_text(
        json.dumps(
            {
                "meta": {"session_id": "s-lane"},
                "messages": [
                    {"role": "user", "content": "keep-lane"},
                    {"role": "assistant", "content": "yes"},
                ],
            }
        ),
        encoding="utf-8",
    )

    def _resolver(_envelope, _path):  # noqa: ANN001
        return "16934", ()

    monkeypatch.setattr(
        "services.rag.chunkers.projected_messages.default_lane_resolver",
        _resolver,
    )
    assert chunk_file(path) == []


def test_projected_case_marker_drops_matching_section(tmp_path: Path) -> None:
    path = tmp_path / "case.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "Parcel 25.7489 appeal"},
                    {"role": "assistant", "content": "clean-reply"},
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    joined = "\n".join(chunk.text for chunk in chunks)
    assert "25.7489" not in joined
    assert "clean-reply" in joined


def test_messages_with_index_is_not_tape(tmp_path: Path) -> None:
    path = tmp_path / "indexed-transcript.json"
    path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "needle-indexed"},
                    {"role": "assistant", "content": "ok"},
                ],
                "index": [],
            }
        ),
        encoding="utf-8",
    )
    chunks = chunk_file(path)
    _assert_capped(chunks)
    assert any("needle-indexed" in chunk.text for chunk in chunks)


def test_skip_log_dedupes(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = tmp_path / "skip.json"
    path.write_text("{}", encoding="utf-8")
    mtime = path.stat().st_mtime_ns
    with caplog.at_level("INFO", logger="services.rag.chunkers.projected_messages"):
        log_skip(path, mtime, "unsupported_shape")
        log_skip(path, mtime, "unsupported_shape")
    lines = [
        rec.message
        for rec in caplog.records
        if rec.message.startswith("rag_chunk_skip ")
    ]
    assert lines == [f"rag_chunk_skip path={path} reason=unsupported_shape"]
