"""IDE-tab budget: transcript measurement, holder resolution, digest stop class."""

from __future__ import annotations

import json
from pathlib import Path

from bus_watch.digest_budget import GEAR_PRESETS, POLICY_DEFAULTS, build_budget_block, effective_policy
from bus_watch.ide_budget import (
    IDE_BUDGET_SOURCE,
    ide_holder_idle_s,
    ide_holder_transcript,
    ide_transcript_probe_resolved,
    measure_ide_tab,
    measure_transcript,
    newest_resume_transcript,
)
from bus_watch.spawn_pending import idle_ide_forfeit

_TID = "f19ca60c-27d7-43b0-bf7e-4e7d29a9eb03"
_OLD = "06e4b108-1ca1-4593-a172-37b0a40f39e4"


def _write_transcript(
    root: Path, tid: str, *, first_user: str, tool_calls: int, mtime: float
) -> Path:
    path = root / tid / f"{tid}.jsonl"
    path.parent.mkdir(parents=True)
    rows = [
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": first_user}]},
        },
        {
            "role": "assistant",
            "message": {
                "content": [{"type": "text", "text": "ok"}]
                + [{"type": "tool_use", "name": "Read", "input": {"path": "x"}}]
                * tool_calls
            },
        },
        {"type": "turn_ended", "status": "success"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    import os

    os.utime(path, (mtime, mtime))
    return path


def test_measure_transcript_counts_tool_calls_and_tip_cp(tmp_path: Path) -> None:
    path = _write_transcript(
        tmp_path,
        _TID,
        first_user="resume 10479\nhop tip_cp=201",
        tool_calls=7,
        mtime=2.0,
    )
    m = measure_transcript(path)
    assert m["tool_calls"] == 7
    assert m["user_turns"] == 1
    assert m["assistant_msgs"] == 1
    assert m["tip_cp"] == 201
    assert m["bytes"] == path.stat().st_size


def test_holder_transcript_only_for_uuid_ide_holders() -> None:
    assert ide_holder_transcript({"holder": f"ide:{_TID}"}) == _TID
    assert ide_holder_transcript({"holder": "ide:10479"}) is None
    assert ide_holder_transcript({"holder": "sdk:abc-123"}) is None
    assert ide_holder_transcript({}) is None


def test_newest_resume_transcript_prefers_mtime(tmp_path: Path) -> None:
    _write_transcript(
        tmp_path, _OLD, first_user="resume 10479 tip_cp=181", tool_calls=1, mtime=10.0
    )
    _write_transcript(
        tmp_path, _TID, first_user="resume 10479 tip_cp=201", tool_calls=1, mtime=20.0
    )
    _write_transcript(
        tmp_path,
        "0" * 8 + "-0000-0000-0000-" + "0" * 12,
        first_user="resume 10534",
        tool_calls=1,
        mtime=30.0,
    )
    assert newest_resume_transcript("10479", tmp_path) == _TID


def test_measure_ide_tab_estimates_and_raises_stop_class(tmp_path: Path) -> None:
    path = _write_transcript(
        tmp_path, _TID, first_user="resume 10479", tool_calls=200, mtime=5.0
    )
    policy = {"ide_window_tokens": 256_000, "ide_tokens_per_tool_call": 1500}
    ide = measure_ide_tab(
        "10479", {"holder": f"ide:{_TID}"}, policy, transcripts_dir=tmp_path
    )
    assert ide is not None
    assert ide["holder_basis"] == "seat_lock"
    assert ide["used_tokens"] == path.stat().st_size // 4 + 200 * 1500
    budget = build_budget_block(
        used_tokens=ide["used_tokens"],
        window_limit_tokens=ide["window_limit_tokens"],
        model="ide-tab",
        source=IDE_BUDGET_SOURCE,
        scope="liaison_seat",
        epoch=ide["transcript_id"],
        as_of="2026-09-13T04:00:00Z",
        tool_calls=ide["tool_calls"],
    )
    assert budget["stop_class"] == "CONTEXT_BUDGET"
    assert budget["tool_calls"] == 200


def test_measure_ide_tab_falls_back_to_resume_mtime(tmp_path: Path) -> None:
    _write_transcript(
        tmp_path, _TID, first_user="resume 10534", tool_calls=3, mtime=5.0
    )
    ide = measure_ide_tab("10534", {"holder": None}, {}, transcripts_dir=tmp_path)
    assert ide is not None and ide["holder_basis"] == "resume_mtime"
    assert measure_ide_tab("99999", {}, {}, transcripts_dir=tmp_path) is None


def test_digest_estimate_never_raises_stop_class() -> None:
    budget = build_budget_block(
        used_tokens=3_500_000,
        window_limit_tokens=700_000,
        model="x",
        source="digest.estimate",
        scope="liaison_seat",
        epoch="fp",
        as_of="2026-09-13T04:00:00Z",
    )
    assert budget["stop_class"] is None


def test_stale_transcript_probe_blocks_idle_forfeit(tmp_path: Path) -> None:
    """a:33450 — lock shows activity but JSONL is tiny/stale ⇒ no forfeit."""
    _write_transcript(
        tmp_path,
        _TID,
        first_user="resume 10479",
        tool_calls=1,
        mtime=1.0,
    )
    lock = {
        "holder": f"ide:{_TID}",
        "turns_seen": 120,
        "claimed_at": "2026-09-13T16:41:00Z",
    }
    assert ide_transcript_probe_resolved(lock, transcripts_dir=tmp_path) is False
    assert ide_holder_idle_s(lock, transcripts_dir=tmp_path, now=5000.0) is None
    assert (
        idle_ide_forfeit(
            lock,
            register="autonomous",
            policy={"ide_idle_forfeit_s": 1200},
            idle_of=lambda *_a, **_k: 3600.0,
            transcripts_dir=tmp_path,
        )
        is None
    )


def test_live_transcript_probe_allows_idle_forfeit(tmp_path: Path) -> None:
    _write_transcript(
        tmp_path,
        _TID,
        first_user="resume 10479",
        tool_calls=50,
        mtime=1.0,
    )
    lock = {"holder": f"ide:{_TID}", "turns_seen": 120}
    assert ide_transcript_probe_resolved(lock, transcripts_dir=tmp_path) is True
    idle = idle_ide_forfeit(
        lock,
        register="autonomous",
        policy={"ide_idle_forfeit_s": 1200},
        idle_of=lambda *_a, **_k: 3600.0,
        transcripts_dir=tmp_path,
    )
    assert idle is not None
    assert idle["holder"] == lock["holder"]


def test_policy_defaults_grok_successor_and_row_bind() -> None:
    policy = effective_policy({"policy": {}})
    assert policy["successor_model"] == POLICY_DEFAULTS["successor_model"]
    assert policy["successor_model"] == "cursor/grok-4.7"
    assert policy["successor_model_knobs"] == {"effort": "high", "fast": "false"}
    assert policy["row_bind_model"] == "cursor/grok-4.7"
    assert policy["row_bind_model_knobs"] == {"effort": "high", "fast": "false"}


def test_gear_three_presets_grok_successor() -> None:
    assert (
        GEAR_PRESETS["3-wake-on-attention"]["successor_model"] == "cursor/grok-4.7"
    )
    preset_only = effective_policy({"policy": {"gear": "3-wake-on-attention"}})
    assert preset_only["successor_model"] == "cursor/grok-4.7"
    assert preset_only["successor_model_knobs"] == {"effort": "high", "fast": "false"}
    assert preset_only["successor_model_source"] == "gear_preset"
    bound = effective_policy(
        {
            "policy": {
                "gear": "3-wake-on-attention",
                "successor_model": "cursor/grok-4.7",
            }
        }
    )
    assert bound["successor_model_source"] == "override"
    assert bound["ide_window_tokens"] == 256_000
