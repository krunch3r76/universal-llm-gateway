"""grokbot-box-mcp-install.sh stdin / TTY validation (temp HOME)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTALL = _REPO_ROOT / "scripts" / "grokbot-box-mcp-install.sh"


def _run_install(
    tmp_path: Path,
    *extra_args: str,
    stdin: str | None = None,
    header_dest: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    if header_dest is not None:
        env["GROKBOT_HEADER_DEST"] = str(header_dest)
    cmd = ["bash", str(_INSTALL), "--skip-mcp-remote", *extra_args]
    return subprocess.run(
        cmd,
        input=stdin,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.offline
def test_install_rejects_placeholder_token(tmp_path: Path) -> None:
    header = tmp_path / "vortex-mcp-headers"
    proc = _run_install(
        tmp_path,
        "--header-stdin",
        stdin="Authorization: Bearer <token>\n",
        header_dest=header,
    )
    assert proc.returncode == 2
    assert "placeholder" in proc.stderr


@pytest.mark.offline
def test_install_rejects_short_bearer(tmp_path: Path) -> None:
    header = tmp_path / "vortex-mcp-headers"
    proc = _run_install(
        tmp_path,
        "--header-stdin",
        stdin="Authorization: Bearer short\n",
        header_dest=header,
    )
    assert proc.returncode == 2
    assert "header must match" in proc.stderr


@pytest.mark.offline
def test_install_writes_header_via_stdin(tmp_path: Path) -> None:
    header = tmp_path / "hdr" / "vortex-mcp-headers"
    bearer = "a" * 24
    line = f"Authorization: Bearer {bearer}\n"
    proc = _run_install(
        tmp_path,
        "--header-stdin",
        stdin=line,
        header_dest=header,
    )
    assert proc.returncode == 0, proc.stderr
    assert header.is_file()
    assert header.read_text(encoding="utf-8") == f"Authorization: Bearer {bearer}\n"
    assert oct(header.stat().st_mode & 0o777) == oct(0o600)


@pytest.mark.offline
def test_install_refuses_overwrite_without_replace(tmp_path: Path) -> None:
    header = tmp_path / "vortex-mcp-headers"
    header.write_text("Authorization: Bearer " + "b" * 24 + "\n", encoding="utf-8")
    bearer = "c" * 24
    proc = _run_install(
        tmp_path,
        "--header-stdin",
        stdin=f"Authorization: Bearer {bearer}\n",
        header_dest=header,
    )
    assert proc.returncode == 2
    assert ".bak" in proc.stderr or "replace" in proc.stderr.lower()
    assert (header.parent / "vortex-mcp-headers.bak").is_file()


@pytest.mark.offline
def test_install_print_includes_launcher_body(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["bash", str(_INSTALL), "--print"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT / "scripts"),
    )
    assert proc.returncode == 0, proc.stderr
    assert "GROKBOT_MCP_REMOTE_LAUNCHER_EMBED" in proc.stdout
    assert "node_modules/mcp-remote/package.json" in proc.stdout
