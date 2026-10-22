"""Tests for impresario ear patrol and dispatcher wiring."""

from __future__ import annotations

import inspect
import json
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bus_watch.impresario_ear_common import is_ear_turn, maybe_enqueue_after_hop
from bus_watch.impresario_ear_patrol import (
    _TOOL_MUTATION_RE,
    _passive_issue_confirmation_parked,
    _transcript_probe,
    clear_patrol_state_for_tests,
    load_patrol_prescreen_inputs,
    patrol_cooldown_active,
    patrol_mailbox_for_agent,
    prescreen_house,
    run_patrol_pass,
    set_patrol_cooldown_path_for_tests,
    stamp_patrol_cooldown,
)

pytestmark = pytest.mark.offline

_ROOT = "18374"
_TRANSCRIPT = "81fb6960-950d-59c5-88b4-af3839f170d1"
_SPECIMEN_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "patrol_specimen_18374.json"
)


def test_prescreen_ear_echo_when_newest_turn_is_ear() -> None:
    ear = {"from_agent": "impresario-ear", "subject": f"EAR | {_ROOT} — healthy"}
    assert is_ear_turn(ear)
    out = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        activity_age_s=999.0,
        recent_turns=[ear],
    )
    assert out["skip_model"] is True
    assert out["reason"] == "ear_echo"


def test_prescreen_no_ear_echo_when_cursor_turn_is_newer_than_ear() -> None:
    ear = {"from_agent": "impresario-ear", "subject": f"EAR | {_ROOT} — healthy"}
    cursor = {"from_agent": "cursor", "body": "fresh work after ear posted"}
    out = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        activity_age_s=999.0,
        recent_turns=[cursor, ear],
    )
    assert out.get("reason") != "ear_echo"


def test_prescreen_no_ear_echo_when_live_from_cursor_before_older_ear() -> None:
    """GET /turns serializes sender as ``from`` (newest-first)."""
    ear = {"from_agent": "impresario-ear", "subject": f"EAR | {_ROOT} — healthy"}
    cursor = {"from": "cursor", "subject": "status", "body": "working"}
    out = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        activity_age_s=999.0,
        recent_turns=[cursor, ear],
    )
    assert out.get("reason") != "ear_echo"


def test_patrol_prescreen_skips_generating_without_model_call() -> None:
    out = prescreen_house(
        root_id=_ROOT,
        transcript_id=_TRANSCRIPT,
        glass_status="generating",
        activity_age_s=999.0,
    )
    assert out["skip_model"] is True
    assert out["reason"] == "generating"
    assert out["signals"] == []


def test_patrol_cooldown_suppresses_unchanged_fingerprint(tmp_path: Path) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    fp = f"{_ROOT}|{_TRANSCRIPT}|abc123"
    stamp_patrol_cooldown(fp, now=1000.0)
    assert patrol_cooldown_active(fp, now=1000.0 + 30.0) is True


def test_patrol_cooldown_allows_new_turn_hash(tmp_path: Path) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    fp_old = f"{_ROOT}|{_TRANSCRIPT}|hash-a"
    stamp_patrol_cooldown(fp_old, now=1000.0)
    fp_new = f"{_ROOT}|{_TRANSCRIPT}|hash-b"
    assert patrol_cooldown_active(fp_new, now=1000.0 + 30.0) is False


def test_run_dispatcher_pass_calls_patrol_beside_context_hops() -> None:
    from bus_watch import endpoint_wake_dispatcher as disp

    src = inspect.getsource(disp.run_dispatcher)
    assert "check_cursor_ide_context_hops" in src
    assert "run_patrol_pass" in src


def test_patrol_specimen_18374_promised_act_hop_failed_quiet_unmerged(
    tmp_path: Path,
) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    payload = json.loads(_SPECIMEN_FIXTURE.read_text(encoding="utf-8"))
    out = prescreen_house(**payload)
    for signal in (
        "promised_act_not_fired",
        "hop_failed_stranded",
        "false_quiet_with_pending_work",
        "unharvested_or_unlanded_child",
    ):
        assert signal in out["signals"]
    assert "team_dispatch" in out["imperative"]
    assert "7139b60" in out["imperative"]
    fp = out["fingerprint"]
    stamp_patrol_cooldown(fp, now=time.time())
    again = prescreen_house(**payload)
    assert again.get("reason") == "cooldown"


def test_run_patrol_pass_skips_enqueue_on_cooldown_even_with_signals(
    tmp_path: Path,
) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    payload = json.loads(_SPECIMEN_FIXTURE.read_text(encoding="utf-8"))
    fp = prescreen_house(**payload)["fingerprint"]
    clock = time.time()
    stamp_patrol_cooldown(fp, now=clock)
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": _ROOT,
            "agent_id": _TRANSCRIPT,
            "glass_status": "completed",
        }
    ]
    admitted: list[dict[str, Any]] = []

    def _admit(opts: dict[str, Any], **_: Any) -> None:
        admitted.append(opts)

    with patch(
        "bus_watch.impresario_ear_patrol.load_patrol_prescreen_inputs",
        return_value=payload,
    ):
        stats = run_patrol_pass(store, now=clock + 30.0, admit_pipeline=_admit)
    assert stats["enqueued"] == 0
    assert admitted == []


def test_patrol_specimen_fixture_loads_through_run_patrol_pass(tmp_path: Path) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    payload = json.loads(_SPECIMEN_FIXTURE.read_text(encoding="utf-8"))
    store = MagicMock()
    store.subscriptions.return_value = [
        {
            "surface": "cursor",
            "thread_scope": _ROOT,
            "agent_id": _TRANSCRIPT,
            "glass_status": "completed",
        }
    ]
    admitted: list[dict[str, Any]] = []

    def _admit(opts: dict[str, Any], **_: Any) -> None:
        admitted.append(opts)

    with patch(
        "bus_watch.impresario_ear_patrol.load_patrol_prescreen_inputs",
        return_value=payload,
    ):
        stats = run_patrol_pass(store, now=10_000.0, admit_pipeline=_admit)
    assert stats["enqueued"] >= 1
    assert admitted[0]["root_thread"] == _ROOT
    assert admitted[0]["to_agent"] == "cursor"


def test_patrol_pass_enqueues_grok_mailbox() -> None:
    clear_patrol_state_for_tests()
    store = MagicMock()

    def _subscriptions(agent: str = "cursor") -> list[dict[str, Any]]:
        if agent != "grok-bot":
            return []
        return [
            {
                "surface": "grok",
                "thread_scope": _ROOT,
                "agent_id": "grok-tab",
                "glass_status": "completed",
            }
        ]

    store.subscriptions.side_effect = _subscriptions
    admitted: list[dict[str, Any]] = []

    def _admit(opts: dict[str, Any], **_: Any) -> None:
        admitted.append(opts)

    tail = (
        "Where this is going: review lane commit 7139b60eec14. "
        "Who carries it: this seat."
    )
    with patch(
        "bus_watch.impresario_ear_patrol.load_patrol_prescreen_inputs",
        return_value={
            "root_id": _ROOT,
            "transcript_id": "grok-tab",
            "glass_status": "completed",
            "activity_age_s": 120.0,
            "in_flight": 0,
            "assistant_tail": tail,
            "tool_blob": "",
            "tick": {"now_row": "quiet"},
            "hop_failed": False,
            "unmerged_child": True,
            "recent_turns": [],
        },
    ):
        stats = run_patrol_pass(store, now=10_000.0, admit_pipeline=_admit)
    assert stats["enqueued"] >= 1
    assert admitted[0]["to_agent"] == "grok-bot"


def test_patrol_mailbox_for_agent() -> None:
    assert patrol_mailbox_for_agent("cursor") == "cursor"
    assert patrol_mailbox_for_agent("grok-bot") == "grok-bot"
    assert patrol_mailbox_for_agent("insyncerator") == "insyncerator"


def test_hop_enqueue_sets_to_agent_for_grok() -> None:
    with patch("bus_watch.impresario_ear_common.enqueue_impresario_ear") as enqueue:
        maybe_enqueue_after_hop(
            root_thread=_ROOT,
            departing_transcript_id="grok-tab",
            channel="hop",
            to_agent="grok-bot",
        )
    opts = enqueue.call_args.args[0]
    assert opts["to_agent"] == "grok-bot"


def test_load_patrol_prescreen_inputs_merges_turns_and_tick(tmp_path: Path) -> None:
    tick_path = tmp_path / f"liaison-{_ROOT}.tick.json"
    tick_path.write_text(
        json.dumps({"policy": {"now_row": "quiet"}, "ready": True}),
        encoding="utf-8",
    )
    store = MagicMock()
    store.turns.return_value = {
        "turns": [
            {
                "from_agent": "cursor",
                "body": "hop failed: phase=keystroke debug_unreachable",
            },
            {
                "from_agent": "cursor-sdk",
                "body": "CLOSEOUT land_disposition: unlanded lane 18671",
            },
        ]
    }
    with patch("bus_watch.impresario_ear_patrol.fetch_giw_active_work_payload", return_value=None), patch(
        "bus_watch.impresario_ear_patrol._transcript_probe",
        return_value=("", "", 120.0, None),
    ):
        loaded = load_patrol_prescreen_inputs(
            store,
            root_id=_ROOT,
            transcript_id=_TRANSCRIPT,
            subscription_row={"glass_status": "completed"},
            watch_dir=tmp_path,
        )
    assert loaded["hop_failed"] is True
    assert loaded["unmerged_child"] is True
    assert loaded["tick"]["now_row"] == "quiet"
    assert len(loaded["recent_turns"]) == 2


_PASSIVE_TAIL = (
    "Confirmed the stale CP residue drift matches the operator report. "
    "Going. Stay here until the next push."
)


def test_tool_mutation_re_ignores_read_only_cortex_and_bus() -> None:
    read_only = (
        '{"type":"tool_use","name":"CallDynamicTool","input":{"namespace":"vortex-code",'
        '"toolName":"cortex","arguments":{"tool":"entity_get"}}}\n'
        '{"type":"tool_use","name":"CallDynamicTool","input":{"namespace":"vortex-code",'
        '"toolName":"agent_bus","arguments":{"tool":"mark_read"}}}\n'
        '{"type":"tool_use","name":"CallDynamicTool","input":{"namespace":"vortex-code",'
        '"toolName":"pipeline","arguments":{"op":"list"}}}'
    )
    assert _TOOL_MUTATION_RE.search(read_only) is None
    mutating = (
        '{"type":"tool_use","name":"CallDynamicTool","input":{"namespace":"vortex-code",'
        '"toolName":"pipeline","arguments":{"op":"run","pipeline_id":"x"}}}'
    )
    assert _TOOL_MUTATION_RE.search(mutating) is not None


def test_transcript_probe_scopes_tool_blob_to_latest_assistant_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bus_watch import impresario_ear_patrol as patrol_mod

    tid = "probe-scope-test"
    tdir = tmp_path / tid
    tdir.mkdir()
    lines = [
        json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "team_dispatch old hop"}]}}),
        json.dumps(
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "older assistant"},
                        {
                            "type": "tool_use",
                            "name": "CallDynamicTool",
                            "input": {"toolName": "agent_bus", "tool": "mark_read"},
                        },
                    ]
                },
            }
        ),
        json.dumps({"role": "user", "message": {"content": [{"type": "text", "text": "operator follow-up"}]}}),
        json.dumps(
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": _PASSIVE_TAIL},
                    ]
                },
            }
        ),
    ]
    (tdir / f"{tid}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    monkeypatch.setattr(patrol_mod, "AGENT_TRANSCRIPTS", tmp_path)
    tail, tool_blob, _age, _glass = _transcript_probe(tid)
    assert _PASSIVE_TAIL.split(".")[0] in tail
    assert tool_blob == ""


def test_prescreen_passive_issue_confirmation_parked_imperative(
    tmp_path: Path,
) -> None:
    set_patrol_cooldown_path_for_tests(tmp_path / "cd.json")
    clear_patrol_state_for_tests()
    for now_row in ("none", "quiet", ""):
        out = prescreen_house(
            root_id=_ROOT,
            transcript_id=_TRANSCRIPT,
            activity_age_s=999.0,
            in_flight=0,
            assistant_tail=_PASSIVE_TAIL,
            tool_blob="",
            tick={"now_row": now_row},
            unmerged_child=True,
        )
        assert "passive_issue_confirmation_parked" in out["signals"]
        assert out["imperative"]
        assert "NEXT_ADMIT" in out["imperative"]
        assert out["skip_model"] is False


def test_passive_issue_not_fired_when_mutating_tool_blob() -> None:
    blob = (
        '{"toolName":"pipeline","op":"run","pipeline_id":"impresario-ear-v1"}'
    )
    assert not _passive_issue_confirmation_parked(
        _PASSIVE_TAIL,
        in_flight=0,
        tool_blob=blob,
        tick={"now_row": "none"},
    )
