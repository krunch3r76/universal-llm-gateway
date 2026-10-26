"""Hermetic HOME pin for scripts/sync-and-restart-mcp.sh (friction 39443 AC2/AC4)."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "sync-and-restart-mcp.sh"
_PIN_START = 'OWNER_HOME="$(getent passwd "$(id -u)" | cut -d: -f6)"'
_PYTHON_LINE = 'PYTHON="${PYTHON:-$HOME/.venvs/universal/bin/python}"'


def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def _head_through_pin() -> str:
    """Script source from the top through the pin, stopping before PYTHON."""
    text = _script_text()
    pin_at = text.index(_PIN_START)
    python_at = text.index(_PYTHON_LINE)
    assert pin_at < python_at
    workspace_at = text.index('WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"')
    assert workspace_at < pin_at
    return text[:python_at] + 'printf "%s\\n" "$HOME"\n'


def _passwd_home() -> str:
    proc = subprocess.run(
        ["getent", "passwd", str(os.getuid())],
        capture_output=True,
        text=True,
        check=True,
    )
    field = proc.stdout.split(":", 6)[5]
    assert field.startswith("/"), proc.stdout
    return field


def _run_head(
    head: str,
    tmp_path: Path,
    *,
    home: str,
    path_prefix: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = home
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env.get('PATH', '')}"
    return subprocess.run(
        ["bash", "-c", head],
        cwd=str(_REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _stub_bins(tmp_path: Path, getent_stdout: str) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker_marker = tmp_path / "docker-invoked"
    getent = bin_dir / "getent"
    payload = tmp_path / "getent-out"
    payload.write_text(getent_stdout, encoding="utf-8")
    getent.write_text(
        f"#!/bin/bash\ncat {payload}\n",
        encoding="utf-8",
    )
    getent.chmod(getent.stat().st_mode | stat.S_IXUSR)
    docker = bin_dir / "docker"
    docker.write_text(
        f"#!/bin/bash\ntouch {docker_marker}\nexit 0\n",
        encoding="utf-8",
    )
    docker.chmod(docker.stat().st_mode | stat.S_IXUSR)
    return bin_dir, docker_marker


def test_pin_sits_between_workspace_root_and_python() -> None:
    text = _script_text()
    workspace_at = text.index('WORKSPACE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"')
    pin_at = text.index(_PIN_START)
    python_at = text.index(_PYTHON_LINE)
    assert workspace_at < pin_at < python_at
    pin_body = text[pin_at:python_at]
    assert "$HOME" not in pin_body
    assert "operator_real_home" not in pin_body


def test_overlay_home_is_replaced_by_passwd_field() -> None:
    """Process HOME is an overlay; the pin exports passwd field 6."""
    overlay = "/tmp/fake-dispatch-overlay-home-39443"
    passwd = _passwd_home()
    assert passwd != overlay
    proc = _run_head(_head_through_pin(), Path("/tmp"), home=overlay)
    assert proc.returncode == 0, proc.stderr
    pinned = proc.stdout.strip().splitlines()[-1]
    assert pinned == passwd
    expand = subprocess.run(
        [
            "bash",
            "-c",
            _head_through_pin()
            + "python3 -c \"from pathlib import Path; print(Path('~/.cortex').expanduser())\"\n",
        ],
        cwd=str(_REPO_ROOT),
        env={**os.environ, "HOME": overlay},
        capture_output=True,
        text=True,
        check=False,
    )
    assert expand.returncode == 0, expand.stderr
    assert expand.stdout.strip().splitlines()[-1] == f"{passwd}/.cortex"


def test_missing_passwd_home_refuses_before_docker(tmp_path: Path) -> None:
    """Empty getent field 6 exits 1 with ERROR on stderr (not 127); no docker."""
    bin_dir, docker_marker = _stub_bins(tmp_path, "io:x:1:1:::\n")
    proc = _run_head(
        _head_through_pin(),
        tmp_path,
        home=str(tmp_path / "overlay"),
        path_prefix=bin_dir,
    )
    assert proc.returncode == 1, proc.stderr
    assert proc.returncode != 127
    assert "ERROR: passwd home" in proc.stderr
    assert "refusing mcp compose" in proc.stderr
    assert not docker_marker.exists()
