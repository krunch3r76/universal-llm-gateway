"""Deferred maestro report delivery (forks 1-A, 2-B, 3-B)."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from scripts.model_manager.guarded_manage_reexec import __main__ as cli
from scripts.model_manager.guarded_manage_reexec.deferred_execute import (
    DeferredRecycleContext,
    run_deferred_recycle,
)
from scripts.model_manager.guarded_manage_reexec.deferred_gate import (
    post_maestro_reply,
    report_deferred_crash,
)
from scripts.model_manager.guarded_manage_reexec.result import GuardedReexecResult
from scripts.model_manager.guarded_manage_reexec.tests import (
    test_deferred_execute as te,
)


def _install_stub_bus(
    repo_root: Path,
    *,
    script_body: str,
    log_path: Path,
) -> None:
    bus_dir = repo_root / "scripts"
    bus_dir.mkdir(parents=True, exist_ok=True)
    stub = bus_dir / "agent-bus"
    stub.write_text(script_body, encoding="utf-8")
    stub.chmod(0o755)
    log_path.write_text("[]", encoding="utf-8")


def _ctx(tmp_path: Path, **overrides: Any) -> DeferredRecycleContext:
    defaults: dict[str, Any] = {
        "parent_dispatch_id": "parent-1",
        "report_thread": "15851",
        "report_after_turn": 5,
        "target_ref": "abc",
        "tmux_target": "0:0",
        "result_path": tmp_path / "result.json",
        "repo_root": tmp_path,
    }
    defaults.update(overrides)
    return DeferredRecycleContext(**defaults)


def _reply_only_stub(log_path: Path) -> str:
    return textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            print(json.dumps({{"turns": [{{"turn_number": 10, "from": "x", "to": "y"}}]}}))
            sys.exit(0)
        if cmd == "reply":
            if "--after-turn" in sys.argv:
                i = sys.argv.index("--after-turn")
                sent = int(sys.argv[i + 1])
                if sent < 10:
                    print("agent-bus error: 409 Conflict", file=sys.stderr)
                    sys.exit(1)
            print(json.dumps({{"turn_number": 11}}))
            sys.exit(0)
        sys.exit(2)
        """
    )


def test_ac1_log_truncation_and_redaction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    log_path = tmp_path / "calls.json"
    tail = "SHOULD_NOT_APPEAR_IN_CAP"
    long_err = "409 Conflict " + ("x" * 2048) + tail
    body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print(json.dumps({{"turns": [{{"turn_number": 1}}]}}))
            sys.exit(0)
        print({long_err!r}, file=sys.stderr)
        sys.exit(1)
        """
    )
    _install_stub_bus(tmp_path, script_body=body, log_path=log_path)
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    assert report["outcome"] == "failed"
    assert len(report["stderr_head"]) == 2048
    assert tail not in report["stderr_head"]
    captured = capsys.readouterr().err
    assert "409 Conflict" in captured
    assert tail not in captured

    bearer_err = "Bearer secret-token leaked"
    bearer_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print(json.dumps({{"turns": [{{"turn_number": 1}}]}}))
            sys.exit(0)
        print({bearer_err!r}, file=sys.stderr)
        sys.exit(1)
        """
    )
    _install_stub_bus(tmp_path, script_body=bearer_body, log_path=log_path)
    post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    bearer_captured = capsys.readouterr().err
    assert "[redacted]" in bearer_captured
    assert "secret-token" not in bearer_captured

    token_err = "AGENT_BUS_TOKEN=supersecret"
    token_body = bearer_body.replace(bearer_err, token_err)
    _install_stub_bus(tmp_path, script_body=token_body, log_path=log_path)
    post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    err3 = capsys.readouterr().err
    assert "[redacted]" in err3
    assert "supersecret" not in err3


def test_ac2_record_delivered_and_failed(tmp_path: Path) -> None:
    log_path = tmp_path / "calls.json"
    _install_stub_bus(
        tmp_path, script_body=_reply_only_stub(log_path), log_path=log_path
    )
    ctx = _ctx(tmp_path)
    ctx.result_path.write_text('{"status":"refused"}', encoding="utf-8")
    before = json.loads(ctx.result_path.read_text(encoding="utf-8"))
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    assert report["outcome"] == "delivered"
    assert report["turn_number"] == 11

    fail_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print("agent-bus error: fetch failed", file=sys.stderr)
            sys.exit(1)
        print("agent-bus error: boom", file=sys.stderr)
        sys.exit(1)
        """
    )
    _install_stub_bus(tmp_path, script_body=fail_body, log_path=log_path)
    report_fail = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="x",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    assert report_fail["outcome"] == "failed"
    assert report_fail["cause"] == "fetch_latest_failed"
    on_disk = json.loads(ctx.result_path.read_text(encoding="utf-8"))
    on_disk.pop("report", None)
    assert before == on_disk


def test_ac3_deliver_past_unread_gate(tmp_path: Path) -> None:
    log_path = tmp_path / "calls.json"
    _install_stub_bus(
        tmp_path, script_body=_reply_only_stub(log_path), log_path=log_path
    )
    ctx = _ctx(tmp_path, report_after_turn=5)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    calls = json.loads(log_path.read_text(encoding="utf-8"))
    reply_calls = [c for c in calls if c and c[0] == "reply"]
    assert len(reply_calls) == 1
    assert report["outcome"] == "delivered"
    idx = reply_calls[0].index("--after-turn")
    assert int(reply_calls[0][idx + 1]) == 10


def test_ac4_persist_failure_after_ack_post_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    _install_turn_store(tmp_path, state_path)
    ctx = _ctx(tmp_path, result_path=tmp_path / "crash.json")

    def _fail_persist(_result: GuardedReexecResult, _path: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_gate.persist_result_projection",
        _fail_persist,
    )
    with pytest.raises(OSError, match="disk full"):
        report_deferred_crash(ctx, RuntimeError("boom"), "tb")
    calls = json.loads(state_path.read_text(encoding="utf-8"))["calls"]
    assert len([c for c in calls if c[0] == "reply"]) == 1


def test_ac4_no_raise_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    missing = tmp_path / "scripts" / "agent-bus"
    missing.parent.mkdir(parents=True, exist_ok=True)
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    assert report["outcome"] == "failed"
    assert report["attempts"] == 0

    log_path = tmp_path / "calls.json"
    hang_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys, time
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print(json.dumps({{"turns": [{{"turn_number": 1}}]}}))
            sys.exit(0)
        time.sleep(30)
        """
    )
    _install_stub_bus(tmp_path, script_body=hang_body, log_path=log_path)
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_gate.REPLY_TIMEOUT_S",
        0.05,
    )
    report_hang = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    assert report_hang["outcome"] == "failed"

    def _boom(**_k: Any) -> None:
        raise RuntimeError("reply_fn boom")

    report_fn = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
        reply_fn=_boom,
    )
    assert report_fn["outcome"] == "failed"

    non_json_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print(json.dumps({{"turns": [{{"turn_number": 1}}]}}))
            sys.exit(0)
        print("not-json")
        sys.exit(0)
        """
    )
    _install_stub_bus(tmp_path, script_body=non_json_body, log_path=log_path)
    report_ok = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    assert report_ok["outcome"] == "delivered"
    assert report_ok["turn_number"] is None
    assert report_ok["attempts"] == 1
    assert sum(1 for c in json.loads(log_path.read_text()) if c[0] == "reply") == 1

    list_fetch_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print(json.dumps([]))
            sys.exit(0)
        print(json.dumps({{"turn_number": 2}}))
        sys.exit(0)
        """
    )
    _install_stub_bus(tmp_path, script_body=list_fetch_body, log_path=log_path)
    report_list = post_maestro_reply(
        ctx,
        report_status="refused",
        reason="r",
        manage_before={},
        manage_after=None,
        child_pids_before={},
        child_pids_after={},
        exit_code=2,
    )
    assert report_list["outcome"] == "failed"
    assert report_list["cause"] == "fetch_latest_failed"

    def _boom_log(_n: int | None) -> None:
        raise RuntimeError("log sink broken")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_gate._log_reply_success",
        _boom_log,
    )
    _install_stub_bus(tmp_path, script_body=_reply_only_stub(log_path), log_path=log_path)
    report_after_ack = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    assert report_after_ack["outcome"] == "delivered"
    assert report_after_ack["attempts"] == 1


def _turn_store_stub(
    state_path: Path,
    *,
    initial_turn: int = 10,
) -> str:
    return textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        state_path = Path({str(state_path)!r})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["calls"].append(sys.argv[1:])
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            last = int(sys.argv[sys.argv.index("--last") + 1])
            turns = state["turns"][-last:]
            state_path.write_text(json.dumps(state), encoding="utf-8")
            print(json.dumps({{"turns": turns}}))
            sys.exit(0)
        if cmd == "reply":
            subj = sys.argv[sys.argv.index("--subject") + 1]
            body = sys.argv[sys.argv.index("--body") + 1]
            num = max((t["turn_number"] for t in state["turns"]), default={initial_turn}) + 1
            state["turns"].append({{
                "turn_number": num,
                "from": "cursor",
                "to": "maestro",
                "subject": subj,
                "body": body,
            }})
            state_path.write_text(json.dumps(state), encoding="utf-8")
            print(json.dumps({{"turn_number": num}}))
            sys.exit(0)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(2)
        """
    )


def _install_turn_store(tmp_path: Path, state_path: Path, *, initial_turn: int = 10) -> None:
    initial_state = {
        "calls": [],
        "turns": [{"turn_number": initial_turn, "from": "x", "to": "y"}],
    }
    body = _turn_store_stub(state_path, initial_turn=initial_turn)
    _install_stub_bus(tmp_path, script_body=body, log_path=state_path)
    state_path.write_text(json.dumps(initial_state), encoding="utf-8")


def test_ac5_timeout_then_inserted_delivered_single_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys, time
        from pathlib import Path
        state_path = Path({str(state_path)!r})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["calls"].append(sys.argv[1:])
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            n_fetch = len([c for c in state["calls"] if c and c[0] == "fetch"])
            if n_fetch >= 2:
                subj = "DONE|decision:manage-recycle-39439-39455-r3b status=executed"
                state["turns"].append({{
                    "turn_number": 11,
                    "from": "cursor",
                    "to": "maestro",
                    "subject": subj,
                    "body": "status: executed",
                }})
            print(json.dumps({{"turns": state["turns"][-20:]}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        if cmd == "reply":
            time.sleep(30)
            sys.exit(0)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(2)
        """
    )
    initial = {"calls": [], "turns": [{"turn_number": 10, "from": "x", "to": "y"}]}
    _install_stub_bus(tmp_path, script_body=body, log_path=state_path)
    state_path.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_gate.REPLY_TIMEOUT_S",
        0.05,
    )
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    replies = [c for c in state["calls"] if c[0] == "reply"]
    assert len(replies) == 1
    assert report["outcome"] == "delivered"
    assert report["attempts"] == 1


def test_ac5_late_insert_after_timeout_one_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    initial_turns = [{"turn_number": 10, "from": "x", "to": "y"}]
    hang_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys, time
        from pathlib import Path
        state_path = Path({str(state_path)!r})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["calls"].append(sys.argv[1:])
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            if len([c for c in state["calls"] if c and c[0] == "reply"]) >= 1:
                subj = "DONE|decision:manage-recycle-39439-39455-r3b status=executed"
                if not any(t.get("turn_number") == 11 for t in state["turns"]):
                    state["turns"].append({{
                        "turn_number": 11,
                        "from": "cursor",
                        "to": "maestro",
                        "subject": subj,
                        "body": "status: executed",
                    }})
            print(json.dumps({{"turns": state["turns"]}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        if cmd == "reply":
            time.sleep(30)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(0)
        """
    )
    _install_stub_bus(tmp_path, script_body=hang_body, log_path=state_path)
    state_path.write_text(
        json.dumps({"calls": [], "turns": initial_turns}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_gate.REPLY_TIMEOUT_S",
        0.05,
    )
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len([c for c in state["calls"] if c[0] == "reply"]) == 1
    assert len([t for t in state["turns"] if t.get("from") == "cursor"]) == 1
    assert report["outcome"] == "delivered"


def test_ac5_request_not_sent_retry_uses_max_window(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        state_path = Path({str(state_path)!r})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["calls"].append(sys.argv[1:])
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            print(json.dumps({{"turns": state["turns"]}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        if cmd == "reply":
            n = len([c for c in state["calls"] if c and c[0] == "reply"])
            if n == 1:
                print("[Errno 111] Connection refused", file=sys.stderr)
                sys.exit(1)
            subj = sys.argv[sys.argv.index("--subject") + 1]
            body = sys.argv[sys.argv.index("--body") + 1]
            after = int(sys.argv[sys.argv.index("--after-turn") + 1])
            assert after == 15
            state["turns"].append({{
                "turn_number": 16,
                "from": "cursor",
                "to": "maestro",
                "subject": subj,
                "body": body,
            }})
            print(json.dumps({{"turn_number": 16}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(2)
        """
    )
    initial = {
        "calls": [],
        "turns": [
            {"turn_number": 10, "from": "x", "to": "y"},
            {"turn_number": 12, "from": "x", "to": "y"},
            {"turn_number": 15, "from": "x", "to": "y"},
        ],
    }
    _install_stub_bus(tmp_path, script_body=body, log_path=state_path)
    state_path.write_text(json.dumps(initial), encoding="utf-8")
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    assert report["outcome"] == "delivered"
    assert len([c for c in json.loads(state_path.read_text())["calls"] if c[0] == "reply"]) == 2


def test_ac5_server_error_retry_uses_max_window(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        state_path = Path({str(state_path)!r})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["calls"].append(sys.argv[1:])
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            print(json.dumps({{"turns": state["turns"]}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        if cmd == "reply":
            n = len([c for c in state["calls"] if c and c[0] == "reply"])
            if n == 1:
                print("Server error '503'", file=sys.stderr)
                sys.exit(1)
            after = int(sys.argv[sys.argv.index("--after-turn") + 1])
            assert after == 14
            subj = sys.argv[sys.argv.index("--subject") + 1]
            body_txt = sys.argv[sys.argv.index("--body") + 1]
            state["turns"].append({{
                "turn_number": 15,
                "from": "cursor",
                "to": "maestro",
                "subject": subj,
                "body": body_txt,
            }})
            print(json.dumps({{"turn_number": 15}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(2)
        """
    )
    initial = {
        "calls": [],
        "turns": [
            {"turn_number": 10, "from": "x", "to": "y"},
            {"turn_number": 14, "from": "x", "to": "y"},
        ],
    }
    _install_stub_bus(tmp_path, script_body=body, log_path=state_path)
    state_path.write_text(json.dumps(initial), encoding="utf-8")
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    assert report["outcome"] == "delivered"


def test_ac5_timeout_no_match_failed_no_extra_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state_path = tmp_path / "state.json"
    body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys, time
        from pathlib import Path
        state_path = Path({str(state_path)!r})
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["calls"].append(sys.argv[1:])
        state_path.write_text(json.dumps(state), encoding="utf-8")
        cmd = sys.argv[1] if len(sys.argv) > 1 else ""
        if cmd == "fetch":
            print(json.dumps({{"turns": state["turns"]}}))
            state_path.write_text(json.dumps(state), encoding="utf-8")
            sys.exit(0)
        if cmd == "reply":
            time.sleep(30)
        state_path.write_text(json.dumps(state), encoding="utf-8")
        sys.exit(0)
        """
    )
    initial = {"calls": [], "turns": [{"turn_number": 10, "from": "x", "to": "y"}]}
    _install_stub_bus(tmp_path, script_body=body, log_path=state_path)
    state_path.write_text(json.dumps(initial), encoding="utf-8")
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_gate.REPLY_TIMEOUT_S",
        0.05,
    )
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    assert report["outcome"] == "failed"
    assert report["cause"] == "timeout delivery_unverified"
    assert len([c for c in json.loads(state_path.read_text())["calls"] if c[0] == "reply"]) == 1


def test_ac5_no_double_post_race_and_cap(tmp_path: Path) -> None:
    log_path = tmp_path / "calls.json"
    race_body = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            latest = 10 + len([c for c in calls if c and c[0] == "reply"])
            print(json.dumps({{"turns": [{{"turn_number": latest}}]}}))
            sys.exit(0)
        i = sys.argv.index("--after-turn")
        sent = int(sys.argv[i + 1])
        if sent < 12:
            print("agent-bus error: 409 Conflict", file=sys.stderr)
            sys.exit(1)
        print(json.dumps({{"turn_number": sent + 1}}))
        sys.exit(0)
        """
    )
    _install_stub_bus(tmp_path, script_body=race_body, log_path=log_path)
    ctx = _ctx(tmp_path)
    report = post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    calls = json.loads(log_path.read_text(encoding="utf-8"))
    replies = [c for c in calls if c[0] == "reply"]
    assert len(replies) <= 3
    assert report["outcome"] == "delivered"


def test_ac5b_classifier_golden_probe(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[4]
    home = tmp_path / "home"
    gateway = home / ".gateway"
    gateway.mkdir(parents=True)
    (gateway / "mcp.yaml").write_text("AGENT_BUS_TOKEN: test-token\n", encoding="utf-8")
    missing_sock = tmp_path / "missing.sock"
    env = {**os.environ, "HOME": str(home), "AGENT_BUS_SOCK": str(missing_sock)}
    proc1 = subprocess.run(
        [sys.executable, str(repo / "scripts" / "agent-bus"), "fetch", "--thread", "1"],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
        cwd=str(repo),
    )
    idle_sock = tmp_path / "idle.sock"
    bound = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    bound.bind(str(idle_sock))
    bound.close()
    env2 = {**os.environ, "HOME": str(home), "AGENT_BUS_SOCK": str(idle_sock)}
    proc2 = subprocess.run(
        [sys.executable, str(repo / "scripts" / "agent-bus"), "fetch", "--thread", "1"],
        capture_output=True,
        text=True,
        env=env2,
        timeout=15,
        cwd=str(repo),
    )
    stderrs = proc1.stderr + proc2.stderr

    def _one_class(err: str) -> bool:
        hits = [
            "409 Conflict" in err,
            "timed out" in err or "TimeoutExpired" in err,
            "[Errno 2]" in err or "[Errno 111]" in err or "Connection refused" in err,
            "Server error '5" in err,
        ]
        return sum(hits) == 1 or (sum(hits) == 0 and "agent-bus error:" in err)

    assert _one_class(proc1.stderr)
    assert _one_class(proc2.stderr)
    assert "[Errno 2]" in stderrs or "No such file" in stderrs
    assert (
        "[Errno 111]" in stderrs
        or "Connection refused" in stderrs
        or "[Errno 2]" in proc2.stderr
    )


def test_ac7_exit_codes_unchanged_when_report_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.guarded_manage_reexec.deferred_gate import (
        RECYCLE_FLEET_SERVICES,
    )

    log_path = tmp_path / "calls.json"
    fail_stub = textwrap.dedent(
        f"""
        #!/usr/bin/env python3
        import json, sys
        from pathlib import Path
        log = Path({str(log_path)!r})
        calls = json.loads(log.read_text(encoding="utf-8"))
        calls.append(sys.argv[1:])
        log.write_text(json.dumps(calls), encoding="utf-8")
        if sys.argv[1] == "fetch":
            print(json.dumps({{"turns": [{{"turn_number": 1}}]}}))
            sys.exit(0)
        sys.exit(1)
        """
    )
    _install_stub_bus(tmp_path, script_body=fail_stub, log_path=log_path)
    rows = {n: te._idle_row(n) for n in RECYCLE_FLEET_SERVICES}
    rows["_checkout"] = {"head_sha": "abc"}
    rows["mcp"] = {
        **te._idle_row("mcp"),
        "version_evidence": {"authoritative_for_liveness": "process_start_code_version"},
    }

    cases = [
        ("proof-satisfied", 0),
        ("crashed", 1),
        ("refused", 2),
        ("proof-failed", 3),
        ("start-failed", 4),
    ]
    def _make_fake(st: str) -> Any:
        def _fake_reexec(**_k: Any) -> GuardedReexecResult:
            return GuardedReexecResult(
                status=st,
                reason=st,
                dry_run=False,
                target_ref="abc",
                executed=st == "proof-satisfied",
            )

        return _fake_reexec

    for status, expected in cases:
        monkeypatch.setattr(
            "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
            _make_fake(status),
        )
        ctx = _ctx(tmp_path, result_path=tmp_path / f"{status}.json")
        code, _result = run_deferred_recycle(
            ctx,
            manage_call=te._manage_from_rows(rows),
            dispatch_status_fn=lambda _d: {"status": "completed"},
            giw_idle_probe_fn=lambda: (True, None),
            reply_fn=None,
            sleep_fn=lambda _s: None,
        )
        assert code == expected


def test_ac10_argv_constraints(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    _install_turn_store(tmp_path, state_path)
    ctx = _ctx(tmp_path)
    post_maestro_reply(
        ctx,
        report_status="executed",
        manage_before={"pid": 1},
        manage_after={"pid": 2},
        child_pids_before={},
        child_pids_after={},
        exit_code=0,
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for argv in state["calls"]:
        if argv[0] == "reply":
            assert "--from-agent" not in argv
            assert "--mark-read" not in argv
        if argv[0] == "fetch":
            assert "--mark-read" not in argv
            assert "--unread" not in argv
            assert "--to" not in argv
    stored = [t for t in state["turns"] if t.get("from") == "cursor"]
    assert len(stored) == 1
    turn = stored[0]
    assert turn["to"] == "maestro"
    assert turn["subject"].startswith("DONE|decision:manage-recycle-39439-39455-r3b")
    assert turn["body"].startswith("status: executed")


def test_ac2_persist_on_recycle_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log_path = tmp_path / "calls.json"
    _install_stub_bus(
        tmp_path, script_body=_reply_only_stub(log_path), log_path=log_path
    )
    rows = te._proof_rows()
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.deferred_execute.run_guarded_reexec",
        lambda **_k: GuardedReexecResult(
            status="proof-satisfied",
            reason="ok",
            dry_run=False,
            target_ref="abc",
            executed=True,
        ),
    )
    ctx = _ctx(tmp_path)
    code, result = run_deferred_recycle(
        ctx,
        manage_call=te._manage_from_rows(rows),
        dispatch_status_fn=lambda _d: {"status": "completed"},
        giw_idle_probe_fn=lambda: (True, None),
        sleep_fn=lambda _s: None,
    )
    assert code == 0
    on_disk = json.loads(ctx.result_path.read_text(encoding="utf-8"))
    assert on_disk["report"]["outcome"] == "delivered"
    assert result.report is not None


def test_ac4_crash_handler_propagates_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result_path = tmp_path / "out.json"
    state_path = tmp_path / "state.json"
    _install_turn_store(tmp_path, state_path)
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.run_deferred_recycle",
        lambda _ctx: (_ for _ in ()).throw(RuntimeError("status read failed")),
    )

    def _failing_reporter(**_kw: Any) -> None:
        raise RuntimeError("reply_fn boom")

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.report_deferred_crash",
        lambda ctx, exc, tb, **kw: report_deferred_crash(
            ctx, exc, tb, reply_fn=_failing_reporter
        ),
    )
    with pytest.raises(RuntimeError, match="status read failed"):
        cli.main(
            [
                "--execute",
                "--recover",
                "--target-ref",
                "abc",
                "--parent-dispatch-id",
                "d1",
                "--report-thread",
                "15851",
                "--report-after-turn",
                "1293",
                "--result-path",
                str(result_path),
            ]
        )
    on_disk = json.loads(result_path.read_text(encoding="utf-8"))
    assert on_disk["report"]["outcome"] == "failed"
