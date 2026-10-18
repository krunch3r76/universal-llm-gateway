from __future__ import annotations

import json
import os
import pwd
from pathlib import Path
from unittest.mock import patch

import pytest
from research_dialogue._types import Snapshot
from research_dialogue.cli import main as cli_main
from research_dialogue.driver import (
    main as driver_main,
)
from research_dialogue.driver import (
    preflight_guard,
    probe_pids,
    resolve_gateway_dir,
)

pytestmark = pytest.mark.offline


def _passwd_gateway() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir) / ".gateway"


def test_resolve_gateway_dir_ignores_home_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("RESEARCH_DIALOGUE_GATEWAY_DIR", raising=False)
    resolved = resolve_gateway_dir()
    assert resolved == _passwd_gateway()
    assert resolved != tmp_path / ".gateway"


def test_resolve_gateway_dir_env_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_dir = tmp_path / "from-env"
    monkeypatch.setenv("RESEARCH_DIALOGUE_GATEWAY_DIR", str(env_dir))
    assert resolve_gateway_dir() == env_dir
    assert resolve_gateway_dir(str(tmp_path / "explicit")) == tmp_path / "explicit"


def test_probe_pids_reads_override_gateway_dir(tmp_path: Path) -> None:
    gw = tmp_path / "gw"
    gw.mkdir()
    (gw / "cloud-proxy.pid").write_text("42\n", encoding="utf-8")
    ok, pids = probe_pids(
        expect_stargate_pid="7",
        expect_cloud_proxy_pid="42",
        ss_output="pid=7",
        gateway_dir=gw,
    )
    assert ok is True
    assert pids["cloud_proxy_pidfile"] == "42"
    assert pids["cloud_proxy_pidfile_path"] == str(gw / "cloud-proxy.pid")


def test_probe_pids_injected_path_marker() -> None:
    ok, pids = probe_pids(
        expect_stargate_pid="1",
        expect_cloud_proxy_pid="2",
        ss_output="pid=1",
        pidfile_text="2",
    )
    assert ok is True
    assert pids["cloud_proxy_pidfile_path"] == "<injected>"


def test_preflight_missing_pidfile_abort_detail(tmp_path: Path) -> None:
    gw = tmp_path / "empty-gw"
    gw.mkdir()
    tried = str(gw / "cloud-proxy.pid")

    def _snap() -> Snapshot:
        from datetime import UTC, datetime
        from decimal import Decimal

        return Snapshot(
            as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
            healthy=True,
            committed_usd=Decimal("0"),
            open_reservations_usd=Decimal("0"),
            window_start="w",
            ledger_instance_id="i",
            latch_active=False,
            raw={"healthy": True, "ledger_path": "/tmp/x.sqlite"},
        )

    code, rec = preflight_guard(
        expect_stargate_pid="7",
        expect_cloud_proxy_pid="42",
        read_fn=_snap,
        ss_output="pid=7",
        gateway_dir=gw,
        open_wait_max_s=0,
    )
    assert code == 5
    assert rec["abort"] == "pid_changed_or_restart"
    detail = rec["abort_detail"]
    assert tried in detail
    assert "read cloud-proxy pid=<missing>" in detail
    assert "expected stargate pid=7 cloud-proxy pid=42" in detail


def test_cli_forwards_gateway_dir(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "state.json").write_text(json.dumps({"turn_count": 0}), encoding="utf-8")
    gw = tmp_path / "cli-gw"
    with patch("research_dialogue.cli.run_driver", return_value=(0, {})) as rd:
        code = cli_main(
            [
                "turn",
                "--session",
                str(session),
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
                "--gateway-dir",
                str(gw),
            ]
        )
    assert code == 0
    assert rd.call_args.kwargs["gateway_dir"] == str(gw)


def test_cli_smoke_forwards_gateway_dir(tmp_path: Path) -> None:
    gw = tmp_path / "smoke-gw"
    with patch(
        "research_dialogue.cli.preflight_guard", return_value=(5, {"abort": "pid"})
    ) as pg:
        code = cli_main(
            [
                "smoke",
                "--session",
                str(tmp_path),
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
                "--gateway-dir",
                str(gw),
            ]
        )
    assert code == 5
    assert pg.call_args.kwargs["gateway_dir"] == str(gw)


def test_driver_cli_forwards_gateway_dir(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    (session / "state.json").write_text(json.dumps({"turn_count": 0}), encoding="utf-8")
    gw = tmp_path / "driver-gw"
    with patch("research_dialogue.driver.run_driver", return_value=(0, {})) as rd:
        code = driver_main(
            [
                "turn",
                "--session",
                str(session),
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
                "--gateway-dir",
                str(gw),
            ]
        )
    assert code == 0
    assert rd.call_args.kwargs["gateway_dir"] == str(gw)


def test_cli_close_forwards_gateway_dir(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    gw = tmp_path / "close-gw"
    with (
        patch("research_dialogue.cli.preflight_guard", return_value=(0, {})) as pg,
        patch("research_dialogue.cli.run_driver", return_value=(0, {})) as rd,
    ):
        code = cli_main(
            [
                "close",
                "--session",
                str(session),
                "--stop-reason",
                "r",
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
                "--gateway-dir",
                str(gw),
            ]
        )
    assert code == 0
    assert pg.call_args.kwargs["gateway_dir"] == str(gw)
    assert rd.call_args.kwargs["gateway_dir"] == str(gw)


def test_cli_close_no_p4_forwards_gateway_dir_to_preflight(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    gw = tmp_path / "no-p4-gw"
    with (
        patch("research_dialogue.cli.preflight_guard", return_value=(0, {})) as pg,
        patch(
            "research_dialogue.cli.run_close", return_value=(0, {"closed": True})
        ) as rc,
    ):
        code = cli_main(
            [
                "close",
                "--session",
                str(session),
                "--stop-reason",
                "r",
                "--no-p4",
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
                "--gateway-dir",
                str(gw),
            ]
        )
    assert code == 0
    assert pg.call_args.kwargs["gateway_dir"] == str(gw)
    rc.assert_called_once_with(session, stop_reason="r", no_p4=True)


def test_driver_cli_close_forwards_gateway_dir(tmp_path: Path) -> None:
    session = tmp_path / "session"
    session.mkdir()
    gw = tmp_path / "driver-close-gw"
    with patch("research_dialogue.driver.run_driver", return_value=(0, {})) as rd:
        code = driver_main(
            [
                "close",
                "--session",
                str(session),
                "--stop-reason",
                "r",
                "--expect-stargate-pid",
                "1",
                "--expect-cloud-proxy-pid",
                "2",
                "--gateway-dir",
                str(gw),
            ]
        )
    assert code == 0
    assert rd.call_args.kwargs["gateway_dir"] == str(gw)
