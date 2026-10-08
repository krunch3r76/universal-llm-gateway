"""Offline tests for Grok Bot ReadTranscript pages -> continuity envelope."""

from __future__ import annotations

import hashlib
import json

import pytest
from continuity_tape.extract_grokbot_jsonl import (
    TranscriptPageError,
    extract_grokbot_pages_files,
    extract_grokbot_transcript,
    reassemble_pages,
)
from continuity_tape.messages import ContinuityMessagesEnvelope, envelope_wire_dict

pytestmark = pytest.mark.offline


def _u(text: str) -> dict:
    return {"role": "user", "message": {"content": [{"type": "text", "text": text}]}}


def _a(*blocks: dict) -> dict:
    return {"role": "assistant", "message": {"content": list(blocks)}}


def _tool_result(tid: str | None, name: str) -> dict:
    block = {"type": "tool_result", "name": name, "result": {"success": "ok"}}
    if tid is not None:
        block["tool_use_id"] = tid
    return {"role": "user", "message": {"content": [block]}}


RECORDS = [
    _u("[GROK_BOT_HIDDEN_PROMPT] boot"),
    _u("Land the orion house."),
    _a(
        {"type": "thinking", "thinking": "secret chain"},
        {"type": "text", "text": "On it."},
        {"type": "tool_use", "id": "t1", "name": "fs", "input": {}},
    ),
    _tool_result("t1", "fs"),
    _u("[A background task just completed: job 7]"),
    _u("<system_reminder>stay lean</system_reminder>"),
    _a({"type": "tool_use", "name": "agent_bus", "input": {}}),
    _tool_result(None, "agent_bus"),
    _a({"type": "text", "text": "Done."}),
    _u("[event] seat changed"),
    _u("<agent_profile_update>x</agent_profile_update>"),
    _u("<instructions_update>y</instructions_update>"),
    _u("[SAND_HIDDEN_PROMPT] z"),
    _u("Checkpoint now."),
    _a({"type": "text", "text": "Checkpointed."}),
]


def _lines(records: list[dict]) -> list[str]:
    return [json.dumps(r) for r in records]


def _pages(records: list[dict], size: int) -> str:
    """ReadTranscript shape: newest page first, header + trailer per page."""
    lines = _lines(records)
    n = len(lines)
    out: list[str] = []
    hi = n
    while hi >= 1:
        lo = max(1, hi - size + 1)
        out.append(f"Transcript of this conversation, positions {lo}\u2013{hi} of {n}:")
        out.extend(lines[lo - 1 : hi])
        if lo > 1:
            out.append(
                f"Older messages remain: call ReadTranscript again with before={lo}"
            )
        hi = lo - 1
    return "\n".join(out) + "\n"


def test_pages_newest_first_reassemble_oldest_first() -> None:
    lines, info = reassemble_pages(_pages(RECORDS, 4))
    assert lines == _lines(RECORDS)
    assert info["coverage"] == "full"
    assert info["positions"] == [1, len(RECORDS)]


def test_envelope_drops_injections_thinking_and_tool_results() -> None:
    env = extract_grokbot_transcript(
        _pages(RECORDS, 4),
        agent_id="agent-x",
        bus_identity="grok-bot-orion",
        observed_at="2026-10-08T20:00:00Z",
    )
    assert env.meta.surface == "grok"
    assert env.meta.turn_count == 2
    msgs = env.messages
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "Land the orion house."
    assert (
        msgs[1]["content"]
        == "On it.\n\n[tool call: fs]\n\n[tool call: agent_bus]\n\nDone."
    )
    assert msgs[2]["content"] == "Checkpoint now."
    assert all(m["source"] == "grok-bot-jsonl" for m in msgs)
    assert "secret chain" not in json.dumps(envelope_wire_dict(env))
    src = env.meta.sources[0]
    assert src["kind"] == "grok_bot_jsonl"
    assert src["bus_identity"] == "grok-bot-orion"
    assert src["injected_user_dropped"] == 7
    assert src["thinking_blocks_dropped"] == 1
    assert src["tool_result_records_dropped"] == 2
    assert src["tool_pairs_by_id"] == 1
    assert src["tool_pairs_by_order"] == 1
    assert src["tool_use_unpaired"] == 0
    ContinuityMessagesEnvelope.model_validate(envelope_wire_dict(env))


def test_page_size_and_bare_jsonl_give_same_seal() -> None:
    a = extract_grokbot_transcript(_pages(RECORDS, 3), agent_id="x")
    b = extract_grokbot_transcript(_pages(RECORDS, 200), agent_id="x")
    c = extract_grokbot_transcript("\n".join(_lines(RECORDS)) + "\n", agent_id="x")
    assert a.meta.messages_sha256 == b.meta.messages_sha256 == c.meta.messages_sha256
    assert a.meta.source_sha256 == b.meta.source_sha256 == c.meta.source_sha256


def test_prefix_extend_seal() -> None:
    first = extract_grokbot_transcript(_pages(RECORDS[:9], 4), agent_id="x")
    later = extract_grokbot_transcript(_pages(RECORDS, 4), agent_id="x")
    assert later.messages[: len(first.messages)] == first.messages


def test_tail_window_marks_coverage_tail() -> None:
    text = _pages(RECORDS, 4)
    newest_page = text.split("Older messages remain")[0]
    env = extract_grokbot_transcript(newest_page, agent_id="x")
    assert env.meta.coverage == "tail"


def test_gap_between_pages_raises() -> None:
    pages = _pages(RECORDS, 4).split("\n")
    # drop the middle page (second header block)
    headers = [i for i, ln in enumerate(pages) if ln.startswith("Transcript of")]
    broken = pages[: headers[1]] + pages[headers[2] :]
    with pytest.raises(TranscriptPageError):
        reassemble_pages("\n".join(broken))


def test_short_page_raises() -> None:
    text = _pages(RECORDS, 4).replace(json.dumps(RECORDS[-1]) + "\n", "", 1)
    with pytest.raises(TranscriptPageError):
        reassemble_pages(text)


NAME_FIX = "771821c5-de68-4b96-a914-edbb87a2e6bb"
MAESTRO = "a4a3dc5c-1d58-4cc2-8e29-b6baffb26163"
MARK = "[... 12 chars truncated ...]"


def _header_pages(records: list[dict], header: str, *, size: int = 4) -> str:
    """Newest page first. Oldest page carries the start line; newer pages a footer."""
    lines = _lines(records)
    n = len(lines)
    chunks: list[tuple[int, int, list[str]]] = []
    hi = n
    while hi >= 1:
        lo = max(1, hi - size + 1)
        chunks.append((lo, hi, lines[lo - 1 : hi]))
        hi = lo - 1
    chunks.reverse()  # oldest first, then we emit newest first
    out: list[str] = []
    for lo, hi, body in reversed(chunks):
        out.append(header.format(lo=lo, hi=hi, n=n))
        out.extend(body)
        if lo == 1:
            out.append("This is the start of the transcript.")
        else:
            out.append(
                "Older messages remain: call ReadTranscript again with the "
                f"same target and before={lo}."
            )
    return "\n".join(out) + "\n"


def test_by_agent_header_matches_own_conversation_and_bare_jsonl() -> None:
    header = (
        'Transcript of agent "38633 name-fix" ('
        + NAME_FIX
        + "), positions {lo}\u2013{hi} of {n}:"
    )
    paged = _header_pages(RECORDS, header)
    own = _pages(RECORDS, 4)
    bare = "\n".join(_lines(RECORDS)) + "\n"
    a = extract_grokbot_transcript(paged, agent_id=NAME_FIX, observed_at="t")
    b = extract_grokbot_transcript(own, agent_id="agent-x", observed_at="t")
    c = extract_grokbot_transcript(bare, agent_id=NAME_FIX, observed_at="t")
    assert a.meta.coverage == "full"
    assert a.meta.messages_sha256 == b.meta.messages_sha256 == c.meta.messages_sha256
    assert a.meta.sources[0]["header_agent_id"] == NAME_FIX
    assert a.meta.sources[0]["window"] == {
        "lo": 1,
        "hi": len(RECORDS),
        "total": len(RECORDS),
    }


def test_maestro_parentheses_and_messages_of_header() -> None:
    maestro = (
        'Transcript of agent "Maestro Sidekick (retired)" ('
        + MAESTRO
        + "), positions {lo}\u2013{hi} of {n}:"
    )
    env = extract_grokbot_transcript(
        _header_pages(RECORDS[:2], maestro, size=2),
        agent_id=MAESTRO,
        observed_at="t",
    )
    assert env.meta.sources[0]["header_agent_id"] == MAESTRO
    messages_of = "Messages of lane 15910, positions {lo}\u2013{hi} of {n}:"
    env2 = extract_grokbot_transcript(
        _header_pages(RECORDS[:2], messages_of, size=2),
        agent_id="x",
        observed_at="t",
    )
    assert env2.meta.sources[0]["header_target"] == "lane 15910"
    assert "header_agent_id" not in env2.meta.sources[0]


def test_target_and_agent_id_mismatch_raise() -> None:
    one = _header_pages(
        RECORDS[:2], "Transcript of alpha, positions {lo}-{hi} of {n}:", size=2
    )
    two = _header_pages(
        RECORDS[:2], "Transcript of beta, positions {lo}-{hi} of {n}:", size=2
    )
    with pytest.raises(TranscriptPageError):
        reassemble_pages(one + two)
    header = f'Transcript of agent "n" ({NAME_FIX}), positions {{lo}}-{{hi}} of {{n}}:'
    with pytest.raises(TranscriptPageError):
        extract_grokbot_transcript(
            _header_pages(RECORDS[:1], header, size=1),
            agent_id="not-the-header",
        )


def test_tool_role_result_dropped_and_paired() -> None:
    records = [
        _a({"type": "tool_use", "id": "toolu_01", "name": "", "input": {}}),
        {
            "role": "tool",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_01",
                        "name": "SendToUser",
                        "result": "Message sent to user. (id: t5s0)",
                    }
                ]
            },
        },
        _a({"type": "text", "text": "sent"}),
    ]
    env = extract_grokbot_transcript(
        "\n".join(_lines(records)) + "\n", agent_id="x", observed_at="t"
    )
    assert env.meta.sources[0]["tool_result_records_dropped"] == 1
    assert env.meta.sources[0]["tool_pairs_by_id"] == 1
    assert "SendToUser" in env.messages[1]["content"]


def test_truncation_counts_dropped_and_kept_parts() -> None:
    records = [
        _a(
            {"type": "thinking", "thinking": f"hidden {MARK}"},
            {"type": "text", "text": f"visible {MARK}"},
        )
    ]
    env = extract_grokbot_transcript(
        "\n".join(_lines(records)) + "\n", agent_id="x", observed_at="t"
    )
    src = env.meta.sources[0]
    assert src["truncated_parts"] == 2
    assert src["truncated_parts_kept"] == 1
    assert env.meta.truncated is True
    clean = extract_grokbot_transcript(
        _pages(RECORDS, 4), agent_id="x", observed_at="t"
    )
    assert clean.meta.sources[0]["truncated_parts"] == 0
    assert clean.meta.sources[0]["truncated_parts_kept"] == 0
    assert clean.meta.truncated is False


def test_pages_sha_capture_and_provenance() -> None:
    text = _pages(RECORDS, 4)
    env = extract_grokbot_transcript(text, agent_id="x", observed_at="t")
    assert (
        env.meta.sources[0]["pages_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    )
    assert not hasattr(env.meta, "capture") or getattr(env.meta, "capture", None) in (
        None,
    )
    wire = envelope_wire_dict(env)
    assert "capture" not in wire["meta"]
    path_text = text
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "page.pages.txt"
        path.write_bytes(path_text.encode())
        poured = extract_grokbot_pages_files([path], agent_id="x", observed_at="t")
        blob = poured.meta.sources[0]["pages_files"]
        file_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        assert blob == [
            {"path": str(path), "sha256": file_sha, "bytes": path.stat().st_size}
        ]
        assert poured.meta.capture == {
            "kind": "dom_harvest",
            "ref": str(path),
            "sha256": file_sha,
            "pseudo_byte_exact": True,
        }
        overridden = extract_grokbot_pages_files(
            [path],
            agent_id="x",
            observed_at="t",
            capture_ref="cortex://notes/system/tape/captures/grok/x/t.pages.txt",
        )
        assert overridden.meta.capture["ref"].startswith("cortex://")
        assert overridden.meta.capture["sha256"] == file_sha
    assert env.meta.provenance == "raw"
    assert env.meta.sources[0]["producer"] == {
        "id": "grok-bot-jsonl-extractor",
        "kind": "extractor",
        "version": "2",
    }
    assert env.meta.sources[0]["window"]["lo"] == 1
    checked = ContinuityMessagesEnvelope.model_validate(wire)
    again = envelope_wire_dict(checked)
    assert again["meta"]["provenance"] == "raw"


def test_seal_stable_across_capture_ref_and_observed_at() -> None:
    text = _pages(RECORDS, 3)
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "a.pages.txt"
        path.write_text(text, encoding="utf-8")
        a = extract_grokbot_pages_files([path], agent_id="x", observed_at="t1")
        b = extract_grokbot_pages_files(
            [path],
            agent_id="x",
            observed_at="t2",
            capture_ref="cortex://notes/system/tape/captures/grok/x/other.pages.txt",
        )
        assert a.meta.messages_sha256 == b.meta.messages_sha256
        assert a.meta.source_sha256 == b.meta.source_sha256
        assert (
            a.meta.sources[0]["pages_files"][0]["sha256"]
            == b.meta.sources[0]["pages_files"][0]["sha256"]
        )
        assert a.meta.sources[0]["reassembled_jsonl_sha256"] == a.meta.source_sha256
        assert a.meta.sources[0]["pages_files"][0]["sha256"] != a.meta.source_sha256
        assert envelope_wire_dict(a) != envelope_wire_dict(b)
