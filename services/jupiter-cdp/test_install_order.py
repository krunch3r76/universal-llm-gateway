"""Offline install-order tests for services/jupiter-cdp/install.sh."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any, TypedDict

import pytest
from claude_bundles.x_display_capacity import (
    X_MAX_CLIENTS_DEFAULT,
    _maxclients_in_cmdlines,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_INSTALL_SH = _REPO / "services" / "jupiter-cdp" / "install.sh"
_PINS = _REPO / "services" / "jupiter-cdp" / "pins.toml"
_XVFB_UNIT = _REPO / "services" / "jupiter-cdp" / "jupiter-cdp-xvfb@.service"

_UNITS = (
    "jupiter-cdp-xvfb@.service",
    "cdp-lane@.service",
    "web-fetcher.service",
    "cdp-ask.service",
    "jupiter-cdp.target",
)
_REPO_CONFS = (
    "cdp-lane@.service.d/repo.conf",
    "web-fetcher.service.d/repo.conf",
    "cdp-ask.service.d/repo.conf",
)
_SYSTEMCTL_VERBS = frozenset({"daemon-reload", "enable", "is-enabled"})

_SYSTEMCTL_STUB = """#!/bin/sh
printf 'systemctl\\t%s\\n' \"$*\" >> \"$STUB_LOG\"
if [ \"$2\" = \"daemon-reload\" ]; then
  find \"$STUB_SNAPSHOT_ROOT\" -mindepth 1 -printf '%P\\t%y\\n' | sort > \"$STUB_SNAPSHOT\"
fi
exit 0
"""

_LOGINCTL_STUB = """#!/bin/sh
printf 'loginctl\\t%s\\n' \"$*\" >> \"$STUB_LOG\"
exit 0
"""


class Installed(TypedDict):
    returncode: int
    stdout: str
    stderr: str
    log: list[tuple[str, list[str]]]
    snap: dict[str, str]
    user_systemd: Path
    pins: dict[str, Any]


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


def _parse_stub_log(text: str) -> list[tuple[str, list[str]]]:
    rows: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        if not line:
            continue
        tool, sep, rest = line.partition("\t")
        assert sep == "\t"
        rows.append((tool, rest.split()))
    return rows


def _parse_snapshot(text: str) -> dict[str, str]:
    snap: dict[str, str] = {}
    for line in text.splitlines():
        if not line:
            continue
        rel, sep, kind = line.partition("\t")
        assert sep == "\t" and rel and kind
        snap[rel] = kind
    return snap


def _fingerprint(repo: Path) -> dict[str, str]:
    root = repo / "services" / "jupiter-cdp"
    paths = [path for path in root.rglob("*") if path.is_file()]
    anchor = repo / "scripts" / "cdp-ask-start"
    assert anchor.is_file(), "F-2 anchor missing: scripts/cdp-ask-start"
    paths.append(anchor)
    return {
        str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def _systemctl_argvs(log: list[tuple[str, list[str]]]) -> list[list[str]]:
    return [argv for tool, argv in log if tool == "systemctl"]


def _execstart_argv(unit_text: str, *, instance: str, home: str) -> list[str]:
    lines = [line for line in unit_text.splitlines() if line.startswith("ExecStart=")]
    assert len(lines) == 1
    value = lines[0].split("=", 1)[1]
    value = value.replace("%I", instance).replace("%h", home)
    return shlex.split(value)


def _assert_returncode(installed: Installed) -> None:
    assert installed["returncode"] == 0, installed["stderr"]


def _which(name: str, path: str) -> Path:
    found = shutil.which(name, path=path)
    assert found is not None, name
    return Path(found)


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Installed:
    root = tmp_path_factory.mktemp("install")
    home = root / "home"
    stub_bin = root / "stub"
    user_systemd = home / ".config" / "systemd" / "user"
    stub_log = root / "stub.log"
    snapshot = root / "snapshot.txt"
    home.mkdir()
    stub_bin.mkdir()
    (root / "run").mkdir()
    _write_executable(stub_bin / "systemctl", _SYSTEMCTL_STUB)
    _write_executable(stub_bin / "loginctl", _LOGINCTL_STUB)
    (stub_bin / "python3").symlink_to(sys.executable)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["USER"] = "tester"
    env["PATH"] = f"{stub_bin}{os.pathsep}{os.environ['PATH']}"
    env.pop("XDG_CONFIG_HOME", None)
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    env.pop("ULG_REPO", None)
    env["XDG_RUNTIME_DIR"] = str(root / "run")
    env["STUB_LOG"] = str(stub_log)
    env["STUB_SNAPSHOT"] = str(snapshot)
    env["STUB_SNAPSHOT_ROOT"] = str(user_systemd)

    assert _which("systemctl", env["PATH"]).parent == stub_bin
    assert _which("loginctl", env["PATH"]).parent == stub_bin
    assert _which("python3", env["PATH"]).parent == stub_bin

    before = _fingerprint(_REPO)
    proc = subprocess.run(
        ["bash", str(_INSTALL_SH)],
        env=env,
        capture_output=True,
        text=True,
    )
    after = _fingerprint(_REPO)
    log_text = stub_log.read_text() if stub_log.is_file() else ""
    log = _parse_stub_log(log_text)

    problems: list[str] = []
    if proc.returncode != 0:
        problems.append(f"F-1 returncode={proc.returncode}\n{proc.stderr}")
    if ("loginctl", ["enable-linger", "tester"]) not in log:
        problems.append("G-d missing loginctl enable-linger tester")
    if not snapshot.exists():
        problems.append("G-e snapshot missing")
    if before != after:
        added = sorted(set(after) - set(before))
        problems.append(f"F-2 fingerprint mismatch added={added}")
    assert not problems, "\n".join(problems)

    with _PINS.open("rb") as handle:
        pins = tomllib.load(handle)
    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "log": log,
        "snap": _parse_snapshot(snapshot.read_text()),
        "user_systemd": user_systemd,
        "pins": pins,
    }


def test_units_and_dropins_exist_before_daemon_reload(installed: Installed) -> None:
    _assert_returncode(installed)
    snap = installed["snap"]
    for name in _UNITS:
        assert snap.get(name) == "l", name
    for name in _REPO_CONFS:
        assert snap.get(name) == "f", name
    assert snap.get("web-fetcher.service.d/pins.conf") == "f"
    lanes = installed["pins"]["lanes"]
    for lane in lanes:
        rel = f"cdp-lane@{lane}.service.d/display.conf"
        assert snap.get(rel) == "f", rel


def test_display_dropin_orders_each_lane_after_its_xvfb(installed: Installed) -> None:
    _assert_returncode(installed)
    user_systemd = installed["user_systemd"]
    for lane, row in installed["pins"]["lanes"].items():
        display = str(row["display"]).lstrip(":")
        expected = (
            "[Unit]\n"
            f"After=jupiter-cdp-xvfb@{display}.service\n"
            f"Upholds=jupiter-cdp-xvfb@{display}.service\n"
        )
        path = user_systemd / f"cdp-lane@{lane}.service.d" / "display.conf"
        assert path.read_text() == expected


def test_enable_after_reload_and_install_never_restarts(installed: Installed) -> None:
    _assert_returncode(installed)
    log = installed["log"]
    for _tool, argv in log:
        assert "--now" not in argv
    calls = _systemctl_argvs(log)
    for argv in calls:
        assert len(argv) >= 2 and argv[1] in _SYSTEMCTL_VERBS, argv
    reload_i = calls.index(["--user", "daemon-reload"])
    target_i = calls.index(["--user", "enable", "jupiter-cdp.target"])
    assert reload_i < target_i
    standing = {
        name for name, row in installed["pins"]["lanes"].items() if row.get("standing")
    }
    enabled: set[str] = set()
    for argv in calls:
        if argv[:2] != ["--user", "enable"] or argv[2] == "jupiter-cdp.target":
            continue
        unit = argv[2]
        prefix = "cdp-lane@"
        suffix = ".service"
        assert unit.startswith(prefix) and unit.endswith(suffix), argv
        enabled.add(unit[len(prefix) : -len(suffix)])
        assert target_i < calls.index(argv)
    assert enabled == standing


def test_xvfb_unit_maxclients_parses_above_code_default(tmp_path: Path) -> None:
    argv = _execstart_argv(
        _XVFB_UNIT.read_text(),
        instance="3",
        home=str(tmp_path),
    )
    parsed = _maxclients_in_cmdlines([argv], ":3")
    assert isinstance(parsed, int)
    assert parsed > X_MAX_CLIENTS_DEFAULT
