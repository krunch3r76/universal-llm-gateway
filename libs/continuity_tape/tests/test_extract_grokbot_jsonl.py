"""Offline tests for Grok Bot ReadTranscript pages -> continuity envelope."""

from __future__ import annotations

import json

import pytest
from continuity_tape.extract_grokbot_jsonl import (
    TranscriptPageError,
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
