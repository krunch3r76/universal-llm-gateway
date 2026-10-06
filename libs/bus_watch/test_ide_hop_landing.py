"""Focus target + landing proof for the attended IDE hop (hop 16, 2026-09-12 06:00–06:20Z).

Land-proof amend (CDP 15456#2 / a:38362): exact ``(resume R, tip_cp=N(?!\\d))`` +
Liaison line — cases A–D below.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bus_watch.ide_hop import (
    build_ide_hop_message,
    fire_ide_hop,
    live_watcher_labels,
    remote_launch_command,
)
from bus_watch.ide_hop_landing import (
    AGENTS_WINDOW_TITLE,
    find_land_via_fence,
    find_transcript_with_hop_header,
    first_line_matches_land,
    focus_title_for,
    hop_header_line,
    hop_land_identity,
    land_find_telemetry,
    wait_for_landed_transcript,
)

pytestmark = pytest.mark.offline


def test_focus_title_defaults_to_the_agents_window_and_honours_policy() -> None:
    assert focus_title_for() == AGENTS_WINDOW_TITLE == "Cursor Agents"
    assert (
        focus_title_for("universal-llm-gateway [SSH: io]")
        == "universal-llm-gateway [SSH: io]"
    )


def test_hop_header_line_is_the_landing_marker() -> None:
    message = build_ide_hop_message("10479", row="x", arm_labels=[], tip_cp_ordinal=147)
    assert hop_header_line(message).startswith(
        "Liaison IDE hop (attended register) tip_cp=147"
    )
    assert "Hop only when autonomous follow-up remains" in message
    assert "STAY" in message
    assert "LOAD the liaison skill (do not skim)" in message
    assert "LOAD liaison-cursor" in message
    assert "team_dispatch(seat=cursor-sdk, job=implement, lane=B)" in message
    assert "repo-write goals → cursor-auto" not in message
    assert "Skip CreateGoal" in message
    assert "--heartbeat 1200" in message
    assert "LOOP: rebuild" in message
    assert "runbook:bus-consult-watcher" in message
    assert message.rstrip().endswith(
        "A finished watch with no next leg is not a situation report."
    )


def test_hop_arm_line_attaches_tail() -> None:
    message = build_ide_hop_message(
        "10479", row="x", arm_labels=["10479-r1-closeout"]
    )
    assert "ARM: tail 10479-r1-closeout" in message
    assert "watch-supervise.sh tail --label 10479-r1-closeout" in message
    assert "start --label" not in message
    assert "§ Peer-house" in message
    assert "Hop after harvest is the rule" not in message


def test_ide_hop_message_keeps_text_past_old_2048_cap() -> None:
    """Paste path used to refuse >2048 bytes so SSH/evdev stays short; that cap is gone."""
    row = "NOW " + ("x" * 5000)
    message = build_ide_hop_message("12088", row=row, arm_labels=["long-label" * 40])
    assert row in message
    assert hop_header_line(message).startswith("Liaison IDE hop")
    assert len(message.encode("utf-8")) > 2048


def test_remote_launch_command_locks_compositor_activate() -> None:
    """Glass path focuses Agents itself; focus_title / no_raise are accepted unused."""
    cmd = remote_launch_command(
        "/repo/tmp/watchers/handoff-messages/m.md",
        remote_repo="/repo",
        focus_title="Cursor Agents",
    )
    assert "glass-launch" in cmd
    assert "--message-file /repo/tmp/watchers/handoff-messages/m.md" in cmd
    assert "--raise-uri" not in cmd
    assert "--palette-query" not in cmd
    defaulted = remote_launch_command("/repo/m.md", remote_repo="/repo")
    assert "glass-launch" in defaulted
    assert "--raise-uri" not in defaulted
    operator = remote_launch_command("/repo/m.md", remote_repo="/repo", no_raise=True)
    assert "glass-launch" in operator
    assert "--focus-title" not in operator


def _write_transcript(root: Path, tid: str, first_line: str, mtime: float) -> None:
    d = root / tid
    d.mkdir()
    p = d / f"{tid}.jsonl"
    p.write_text(first_line + "\n", encoding="utf-8")
    os.utime(p, (mtime, mtime))


def _land_line(root: str, tip: int) -> str:
    """Minimal JSONL first line (text field) for offline fixtures."""
    return (
        f'{{"role":"user","text":"resume {root}\\n'
        f'Liaison IDE hop (attended register) tip_cp={tip}. LOAD the liaison skill."}}'
    )


def _wrapped_land_line(root: str, tip: int, *, now_extra: str = "") -> str:
    """Hub shape: message.content[].text with timestamp + user_query wrapper."""
    body = (
        f"resume {root}\n"
        f"Liaison IDE hop (attended register) tip_cp={tip}. LOAD the liaison skill.\n"
        f"NOW: test{now_extra}\n"
    )
    text = (
        "<timestamp>Tuesday, Oct 6, 2026, 10:45 AM (UTC-7)</timestamp>\n"
        f"<user_query>\n{body}</user_query>"
    )
    return json.dumps(
        {
            "role": "user",
            "message": {"content": [{"type": "text", "text": text}]},
        }
    )


def test_hop_land_identity_from_message() -> None:
    message = build_ide_hop_message("15420", row="x", arm_labels=[], tip_cp_ordinal=17)
    assert hop_land_identity(message, root_id="15420") == ("15420", 17)


def test_landing_requires_a_transcript_newer_than_the_fire(tmp_path: Path) -> None:
    """Without tip_cp, mtime gate still applies (resume-only needles are ambiguous)."""
    marker = "Liaison IDE hop (attended register) no tip."
    fired = time.time()
    _write_transcript(tmp_path, "old-tab", f'{{"text": "{marker}"}}', fired - 600)
    found, tel = wait_for_landed_transcript(
        marker,
        since_epoch=fired,
        transcripts_dir=tmp_path,
        timeout_s=0.05,
        poll_s=0.01,
    )
    assert found is None
    assert tel["matches"] == 0
    _write_transcript(tmp_path, "new-tab", f'{{"text": "{marker}"}}', fired + 1)
    found, tel = wait_for_landed_transcript(
        marker,
        since_epoch=fired,
        transcripts_dir=tmp_path,
        timeout_s=0.05,
        poll_s=0.01,
    )
    assert found == "new-tab"
    assert tel["matches"] == 1


def test_land_find_telemetry_lists_exact_needles() -> None:
    tel = land_find_telemetry(
        root_id="15420", tip_cp=17, marker="Liaison IDE hop … tip_cp=17", matches=0
    )
    assert tel == {
        "needles": ["resume 15420", "tip_cp=17", "Liaison IDE hop"],
        "matches": 0,
    }


def test_case_a_number_prefix_does_not_match() -> None:
    """F1 — tip_cp=1 must not match tip_cp=17."""
    line = _land_line("15420", 17)
    assert first_line_matches_land(
        line, root_id="15420", tip_cp=17, marker="unused"
    )
    assert not first_line_matches_land(
        line, root_id="15420", tip_cp=1, marker="unused"
    )


def test_case_b_other_root_does_not_match() -> None:
    """F1 — resume 15441 tip_cp=17 is not a land for root 15420."""
    line = _land_line("15441", 17)
    assert not first_line_matches_land(
        line, root_id="15420", tip_cp=17, marker="unused"
    )
    assert first_line_matches_land(
        line, root_id="15441", tip_cp=17, marker="unused"
    )


def test_case_c_quoted_review_without_liaison_does_not_match() -> None:
    """F1 — review paste quoting tip_cp=17 without Liaison line is not a land."""
    line = (
        '{"role":"user","text":"resume 15420\\n'
        'CDP said tip_cp=17 is wrong; do not merge."}'
    )
    assert not first_line_matches_land(
        line, root_id="15420", tip_cp=17, marker="unused"
    )


def test_tip_in_now_row_does_not_match() -> None:
    """15456#4 F1 — tip_cp on NOW must not satisfy land for a different Liaison tip."""
    line = _wrapped_land_line("15420", 16, now_extra=" verify tip_cp=17 land")
    assert first_line_matches_land(
        line, root_id="15420", tip_cp=16, marker="unused"
    )
    assert not first_line_matches_land(
        line, root_id="15420", tip_cp=17, marker="unused"
    )


def test_wrapped_user_query_real_shape_matches() -> None:
    """15456#4 F3 — hub <user_query> wrapper must still land (specimen d195e491)."""
    line = _wrapped_land_line("15420", 17)
    assert first_line_matches_land(
        line, root_id="15420", tip_cp=17, marker="unused"
    )


def test_quoted_full_hop_in_review_does_not_match() -> None:
    """15456#4 F1 — review quoting a full hop still fails: resume is not first body line."""
    quoted = (
        "CDP review of hop:\n"
        "```\n"
        "resume 15420\n"
        "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill.\n"
        "```\n"
    )
    payload = {
        "role": "user",
        "message": {
            "content": [
                {
                    "type": "text",
                    "text": f"<user_query>\n{quoted}</user_query>",
                }
            ]
        },
    }
    assert not first_line_matches_land(
        json.dumps(payload), root_id="15420", tip_cp=17, marker="unused"
    )


def test_case_d_exact_land_hits_despite_old_mtime(tmp_path: Path) -> None:
    """Exact (root, tip) lands without mtime; tipless find keeps since_epoch."""
    marker = "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill."
    fired = time.time()
    tid = "d195e491-1405-4536-8af2-2496b7b785b5"
    _write_transcript(tmp_path, tid, _wrapped_land_line("15420", 17), fired - 120)
    found, tel = find_transcript_with_hop_header(
        marker, tmp_path, root_id="15420", tip_cp=17
    )
    assert found == tid
    assert tel["matches"] == 1
    assert "resume 15420" in tel["needles"]
    found, tel = wait_for_landed_transcript(
        marker,
        since_epoch=fired,
        transcripts_dir=tmp_path,
        timeout_s=0.05,
        poll_s=0.01,
        root_id="15420",
        tip_cp=17,
    )
    assert found == tid
    tipless_marker = "Liaison IDE hop (attended register) no tip."
    _write_transcript(
        tmp_path,
        "old-tipless",
        f'{{"text": "{tipless_marker}"}}',
        fired - 600,
    )
    found, tel = find_transcript_with_hop_header(
        tipless_marker, tmp_path, since_epoch=fired
    )
    assert found is None
    assert tel["matches"] == 0


def test_find_rejects_wrong_root_even_when_tip_matches(tmp_path: Path) -> None:
    marker = "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill."
    _write_transcript(
        tmp_path, "wrong-root", _land_line("15441", 17), time.time()
    )
    found, tel = find_transcript_with_hop_header(
        marker, tmp_path, root_id="15420", tip_cp=17
    )
    assert found is None
    assert tel["matches"] == 0


def test_find_excludes_departing_transcript(tmp_path: Path) -> None:
    """15456#4 F2 — departing tab must not win pre_existing / find."""
    marker = "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill."
    departing = "aaaa1111-bbbb-cccc-dddd-eeeeeeeeeeee"
    successor = "d195e491-1405-4536-8af2-2496b7b785b5"
    _write_transcript(tmp_path, departing, _wrapped_land_line("15420", 17), time.time())
    found, tel = find_transcript_with_hop_header(
        marker,
        tmp_path,
        root_id="15420",
        tip_cp=17,
        exclude_ids={departing},
    )
    assert found is None
    assert tel["matches"] == 0
    _write_transcript(tmp_path, successor, _wrapped_land_line("15420", 17), time.time())
    found, tel = find_transcript_with_hop_header(
        marker,
        tmp_path,
        root_id="15420",
        tip_cp=17,
        exclude_ids={departing},
    )
    assert found == successor


def test_fire_ide_hop_pre_existing_skips_keystroke(tmp_path: Path) -> None:
    """15456 ask 2 — exact land already present ⇒ ok via pre_existing, no Ctrl+N."""
    marker = "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill."
    message = f"resume 15420\n\n{marker}\nNOW: test\n"
    tid = "d195e491-1405-4536-8af2-2496b7b785b5"
    _write_transcript(tmp_path, tid, _wrapped_land_line("15420", 17), time.time() - 90)
    with (
        patch("bus_watch.ide_hop.durable_write_text"),
        patch("bus_watch.ide_hop.session_unreachable", return_value=None),
        patch("bus_watch.ide_hop.subprocess.run") as run_mock,
        patch("bus_watch.ide_hop.AGENT_TRANSCRIPTS", tmp_path),
    ):
        out = fire_ide_hop(
            message,
            root_id="15420",
            seal={"ok": True, "bus_turn": 1},
            gui_host="orion-node",
            landing_timeout_s=0.01,
        )
    assert out["ok"] is True
    assert out["landed_transcript_id"] == tid
    assert out["landed_via"] == "pre_existing"
    assert out["keystroke"] is None
    run_mock.assert_not_called()


def test_fire_ide_hop_departing_not_pre_existing(tmp_path: Path) -> None:
    """15456#4 F2 — departing tab alone must not skip keystroke as pre_existing."""
    marker = "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill."
    message = f"resume 15420\n\n{marker}\nNOW: test\n"
    departing = "aaaa1111-bbbb-cccc-dddd-eeeeeeeeeeee"
    _write_transcript(
        tmp_path, departing, _wrapped_land_line("15420", 17), time.time() - 90
    )
    proc = MagicMock(returncode=0, stdout='{"ok": true}', stderr="")
    with (
        patch("bus_watch.ide_hop.durable_write_text"),
        patch("bus_watch.ide_hop.session_unreachable", return_value=None),
        patch("bus_watch.ide_hop.subprocess.run", return_value=proc) as run_mock,
        patch("bus_watch.ide_hop.AGENT_TRANSCRIPTS", tmp_path),
        patch("bus_watch.ide_hop.remote_toplevels", return_value=[]),
        patch("bus_watch.ide_hop.try_acquire_hop_mutex", return_value={"ok": True}),
        patch("bus_watch.ide_hop.try_acquire_window_mutex", return_value={"ok": True}),
        patch("bus_watch.ide_hop.release_hop_mutex"),
        patch("bus_watch.ide_hop.release_window_mutex"),
        patch(
            "bus_watch.ide_hop.mark_fired",
            return_value={"root": "15420", "state": "fired"},
        ),
        patch(
            "bus_watch.ide_hop.mark_expired",
            return_value={"root": "15420", "state": "expired"},
        ),
    ):
        out = fire_ide_hop(
            message,
            root_id="15420",
            seal={"ok": True, "bus_turn": 1},
            gui_host="orion-node",
            landing_timeout_s=0.01,
            departing_transcript_id=departing,
            armed_since=lambda *_a, **_k: [],
        )
    assert out["ok"] is False
    assert out["phase"] == "expired"
    run_mock.assert_called_once()


def test_fire_ide_hop_find_transcript_recovery_ok_not_operator_page(
    tmp_path: Path,
) -> None:
    """a:38362 — wait miss while exact land present ⇒ ok via find; ¬ glass-launch ask."""
    marker = "Liaison IDE hop (attended register) tip_cp=17. LOAD the liaison skill."
    message = f"resume 15420\n\n{marker}\nNOW: test\n"
    tid = "d195e491-1405-4536-8af2-2496b7b785b5"
    _write_transcript(tmp_path, tid, _wrapped_land_line("15420", 17), time.time() - 90)
    proc = MagicMock(returncode=0, stdout='{"ok": true, "phase": "glass-launch"}', stderr="")
    with (
        patch("bus_watch.ide_hop.durable_write_text"),
        patch("bus_watch.ide_hop.session_unreachable", return_value=None),
        patch("bus_watch.ide_hop.subprocess.run", return_value=proc),
        patch(
            "bus_watch.ide_hop.wait_for_landed_transcript",
            return_value=(None, {"needles": [], "matches": 0}),
        ),
        # Force past pre_existing so the recovery path runs.
        patch(
            "bus_watch.ide_hop.find_transcript_with_hop_header",
            side_effect=[
                (None, {"needles": [], "matches": 0}),
                (tid, {"needles": ["resume 15420", "tip_cp=17"], "matches": 1}),
            ],
        ),
        patch("bus_watch.ide_hop.AGENT_TRANSCRIPTS", tmp_path),
        patch("bus_watch.ide_hop.remote_toplevels") as toplevels_mock,
        patch("bus_watch.ide_hop.try_acquire_hop_mutex", return_value={"ok": True}),
        patch("bus_watch.ide_hop.try_acquire_window_mutex", return_value={"ok": True}),
        patch("bus_watch.ide_hop.release_hop_mutex"),
        patch("bus_watch.ide_hop.release_window_mutex"),
        patch(
            "bus_watch.ide_hop.mark_fired",
            return_value={"root": "15420", "state": "fired"},
        ),
        patch(
            "bus_watch.ide_hop.mark_landed",
            return_value={"root": "15420", "state": "landed", "proof": "transcript"},
        ),
    ):
        out = fire_ide_hop(
            message,
            root_id="15420",
            seal={"ok": True, "bus_turn": 1},
            gui_host="orion-node",
            landing_timeout_s=0.01,
        )
    assert out["ok"] is True
    assert out["landed_transcript_id"] == tid
    assert out["landed_via"] == "find_transcript"
    assert "glass-launch" not in (out.get("fix") or "")
    toplevels_mock.assert_not_called()


def test_real_d195e491_first_line_matches_when_present() -> None:
    """Decisive falsifier completeness: hub specimen first line vs tip_cp=17."""
    path = Path(
        "/home/io/.cursor/projects/mnt-torus-projects-universal-llm-gateway/"
        "agent-transcripts/d195e491-1405-4536-8af2-2496b7b785b5/"
        "d195e491-1405-4536-8af2-2496b7b785b5.jsonl"
    )
    if not path.is_file():
        pytest.skip("specimen transcript not on this host")
    first = path.read_text(encoding="utf-8").splitlines()[0]
    assert first_line_matches_land(
        first, root_id="15420", tip_cp=17, marker="unused"
    )


def test_live_watcher_labels_treats_predicate_unmet_as_live(tmp_path: Path) -> None:
    """CDP consults sit at predicate_unmet until the first qualifying reply (a:33284)."""
    live = os.getpid()
    (tmp_path / "10534-cdp.state.json").write_text(
        json.dumps({"status": "predicate_unmet", "thread": "10534", "pid": live}),
        encoding="utf-8",
    )
    (tmp_path / "10534-cdp.pid").write_text(str(live), encoding="utf-8")
    (tmp_path / "10534-run.state.json").write_text(
        json.dumps({"status": "running", "thread": "10534", "pid": live}),
        encoding="utf-8",
    )
    (tmp_path / "10534-run.pid").write_text(str(live), encoding="utf-8")
    (tmp_path / "10534-done.state.json").write_text(
        json.dumps({"status": "complete", "thread": "10534", "pid": live}),
        encoding="utf-8",
    )
    (tmp_path / "other-root.state.json").write_text(
        json.dumps({"status": "predicate_unmet", "thread": "10479", "pid": live}),
        encoding="utf-8",
    )
    (tmp_path / "other-root.pid").write_text(str(live), encoding="utf-8")
    labels = live_watcher_labels("10534", watch_dir=tmp_path)
    assert labels == ["10534-cdp", "10534-run"]


def test_live_watcher_labels_excludes_house_prefix(tmp_path: Path) -> None:
    live = os.getpid()
    (tmp_path / "house-12586-a1b2c3.state.json").write_text(
        json.dumps({"status": "polling", "thread": "12586", "pid": live}),
        encoding="utf-8",
    )
    (tmp_path / "house-12586-a1b2c3.pid").write_text(str(live), encoding="utf-8")
    (tmp_path / "12586-r1-closeout.state.json").write_text(
        json.dumps({"status": "polling", "thread": "12586", "pid": live}),
        encoding="utf-8",
    )
    (tmp_path / "12586-r1-closeout.pid").write_text(str(live), encoding="utf-8")
    assert live_watcher_labels("12586", watch_dir=tmp_path) == [
        "12586-r1-closeout",
    ]


def test_live_watcher_labels_excludes_own_lane(tmp_path: Path) -> None:
    """A seat must not read the watcher on its own closeout as follow-up.

    Specimen 10534-ticker-opus-10579-closeout (2026-09-12): the ticker arms a
    closeout watcher on the lane it spawns, so the successor sitting in lane
    10579 saw a live tail, would hop, and would spawn a seat with the same
    watcher — an unbounded premium chain.
    """
    live = os.getpid()
    for stem, thread in (("10534-own-lane", "10579"), ("10534-peer", "10534")):
        (tmp_path / f"{stem}.state.json").write_text(
            json.dumps({"status": "polling", "thread": thread, "pid": live}),
            encoding="utf-8",
        )
        (tmp_path / f"{stem}.pid").write_text(str(live), encoding="utf-8")
    assert live_watcher_labels("10534", watch_dir=tmp_path) == [
        "10534-own-lane",
        "10534-peer",
    ]
    assert live_watcher_labels(
        "10534", watch_dir=tmp_path, exclude_threads=["10579"]
    ) == ["10534-peer"]


def test_live_watcher_labels_skips_dead_predicate_unmet(tmp_path: Path) -> None:
    """Dead stall must not ARM a hang-tail (10479-r4-consult, 2026-09-11)."""
    (tmp_path / "10479-r4-consult.state.json").write_text(
        json.dumps(
            {
                "status": "predicate_unmet",
                "thread": "10479",
                "pid": 384667,
            }
        ),
        encoding="utf-8",
    )
    labels = live_watcher_labels("10479", watch_dir=tmp_path)
    assert labels == []


def test_tipless_land_requires_resume_root_not_marker_alone() -> None:
    """a:38439 — shared Liaison template must not match a foreign root."""
    marker = "Liaison IDE hop (attended register). LOAD the liaison skill (do not skim)."
    foreign = (
        '{"role":"user","text":"resume 12286\\n'
        f'{marker}\\nNOW: old"}}'
    )
    ours = (
        '{"role":"user","text":"resume 15420\\n'
        f'{marker}\\nNOW: collective"}}'
    )
    assert not first_line_matches_land(
        foreign, root_id="15420", tip_cp=None, marker=marker
    )
    assert first_line_matches_land(
        ours, root_id="15420", tip_cp=None, marker=marker
    )


def test_find_transcript_tipless_with_since_epoch_skips_old_foreign(
    tmp_path: Path,
) -> None:
    """find_transcript without tip must not revive Sep-era foreign hops."""
    marker = "Liaison IDE hop (attended register). LOAD the liaison skill (do not skim)."
    fired = time.time()
    foreign = (
        f'{{"role":"user","text":"resume 12286\\n{marker}\\nNOW: old"}}'
    )
    ours = (
        f'{{"role":"user","text":"resume 15420\\n{marker}\\nNOW: collective"}}'
    )
    _write_transcript(tmp_path, "old-foreign", foreign, fired - 600)
    found, tel = find_transcript_with_hop_header(
        marker,
        tmp_path,
        root_id="15420",
        tip_cp=None,
        since_epoch=fired,
    )
    assert found is None
    assert tel["matches"] == 0
    assert "resume 15420" in tel["needles"]
    _write_transcript(tmp_path, "new-ours", ours, fired + 1)
    found, tel = find_transcript_with_hop_header(
        marker,
        tmp_path,
        root_id="15420",
        tip_cp=None,
        since_epoch=fired,
    )
    assert found == "new-ours"
    assert tel["matches"] == 1


def test_wait_lands_when_jsonl_appears_after_30s(tmp_path: Path, monkeypatch) -> None:
    """AC1 — JSONL born well after the old 30s window still lands (a:38474)."""
    marker = "Liaison IDE hop (attended register) tip_cp=75. LOAD the liaison skill."
    tid = "6d365a29-dddf-4036-a6e0-2c8b5a7ea8df"
    fired = time.time()
    polls = {"n": 0}

    def fake_sleep(_s: float) -> None:
        polls["n"] += 1
        if polls["n"] == 1:
            # Simulate birth at +455s of wall time relative to fire — file appears mid-wait.
            _write_transcript(
                tmp_path, tid, _wrapped_land_line("15441", 75), fired + 455.0
            )

    monkeypatch.setattr(time, "sleep", fake_sleep)
    found, tel = wait_for_landed_transcript(
        marker,
        since_epoch=fired,
        transcripts_dir=tmp_path,
        timeout_s=10.0,
        poll_s=0.01,
        root_id="15441",
        tip_cp=75,
        fence_first=True,
        armed_since=lambda *_a, **_k: [],
    )
    assert found == tid
    assert tel.get("proof") == "transcript"
    assert polls["n"] >= 1


def test_wait_fence_first_lands_before_jsonl(tmp_path: Path) -> None:
    """Fence armed row proves land without any JSONL on disk."""
    marker = "Liaison IDE hop (attended register) tip_cp=33. LOAD the liaison skill."
    tid = "0b84aeac-7a9e-480a-8291-ae032c0513b4"
    fired = time.time()

    def armed_since(root, *, since_epoch, exclude_transcript_ids=None):
        assert root == "15420"
        return [
            {
                "fence_id": "rf-test",
                "transcript_id": tid,
                "created_epoch": since_epoch + 1.0,
            }
        ]

    found, tel = wait_for_landed_transcript(
        marker,
        since_epoch=fired,
        transcripts_dir=tmp_path,
        timeout_s=2.0,
        poll_s=0.01,
        root_id="15420",
        tip_cp=33,
        exclude_ids={"departing-tab"},
        armed_since=armed_since,
    )
    assert found == tid
    assert tel.get("proof") == "fence"
    assert not (tmp_path / tid).exists()


def test_wait_fence_excludes_departing_transcript(tmp_path: Path) -> None:
    marker = "Liaison IDE hop (attended register) tip_cp=1. LOAD the liaison skill."
    departing = "70d485fe-b09d-435c-8c50-d5dde2d9f9c4"
    fired = time.time()

    def armed_since(root, *, since_epoch, exclude_transcript_ids=None):
        assert departing in (exclude_transcript_ids or set())
        return []

    found, _tel = wait_for_landed_transcript(
        marker,
        since_epoch=fired,
        transcripts_dir=tmp_path,
        timeout_s=0.05,
        poll_s=0.01,
        root_id="15441",
        tip_cp=1,
        exclude_ids={departing},
        armed_since=armed_since,
    )
    assert found is None


def test_find_land_via_fence_default_probe_uses_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """a:38474 review B1 — default path must not open SQLite; HTTP route only."""
    tid = "0b84aeac-7a9e-480a-8291-ae032c0513b4"
    seen: dict[str, object] = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "state": "armed_since",
                "root_thread": "15420",
                "armed": [
                    {
                        "fence_id": 3509,
                        "transcript_id": tid,
                        "created_epoch": 100.5,
                    },
                    {
                        "fence_id": 1,
                        "transcript_id": "departing-tab",
                        "created_epoch": 200.0,
                    },
                ],
            }

    class _Client:
        def __enter__(self) -> "_Client":
            return self

        def __exit__(self, *_a: object) -> None:
            return None

        def get(self, path: str, *, params=None, headers=None):
            seen["path"] = path
            seen["params"] = params
            seen["headers"] = headers
            return _Resp()

    monkeypatch.setattr(
        "bus_watch.digest_budget.agent_bus_bearer_headers",
        lambda: {"Authorization": "Bearer test-token"},
    )
    monkeypatch.setattr(
        "transport_utils.make_sync_client",
        lambda *_a, **_k: _Client(),
    )
    # If the old direct-store path is used, this would be imported — refuse it.
    monkeypatch.setattr(
        "agent_bus_store.resume_fence_store.armed_fences_for_root_since",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("direct DB probe")),
        raising=False,
    )
    found, tel = find_land_via_fence(
        "15420",
        since_epoch=100.0,
        exclude_ids={"departing-tab"},
    )
    assert found == tid
    assert tel.get("proof") == "fence"
    assert seen["path"] == "/threads/15420/resume-fence"
    assert seen["params"] == {"since_epoch": 100.0}
    assert seen["headers"] == {"Authorization": "Bearer test-token"}
