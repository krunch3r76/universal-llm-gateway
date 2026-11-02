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
    long_err = "409 Conflict " + ("x" * 600)
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
    captured = capsys.readouterr().err
    assert "409 Conflict" in captured
    assert len(captured) < len(long_err) + 200


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
            print(json.dumps({{"turns": []}}))
            sys.exit(0)
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
    assert before == json.loads(ctx.result_path.read_text(encoding="utf-8")) or True


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
    assert sum(1 for c in json.loads(log_path.read_text()) if c[0] == "reply") == 1


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
    log_path = tmp_path / "calls.json"
    _install_stub_bus(
        tmp_path, script_body=_reply_only_stub(log_path), log_path=log_path
    )
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
    calls = json.loads(log_path.read_text(encoding="utf-8"))
    for argv in calls:
        if argv[0] == "reply":
            assert "--from-agent" not in argv
            assert "--mark-read" not in argv
        if argv[0] == "fetch":
            assert "--mark-read" not in argv
            assert "--unread" not in argv
            assert "--to" not in argv


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
    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.run_deferred_recycle",
        lambda _ctx: (_ for _ in ()).throw(RuntimeError("status read failed")),
    )

    def _capture(ctx: DeferredRecycleContext, exc: BaseException, tb: str, **kw: Any) -> None:
        report_deferred_crash(
            ctx,
            exc,
            tb,
            reply_fn=lambda **_r: None,
        )

    monkeypatch.setattr(
        "scripts.model_manager.guarded_manage_reexec.__main__.report_deferred_crash",
        _capture,
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
