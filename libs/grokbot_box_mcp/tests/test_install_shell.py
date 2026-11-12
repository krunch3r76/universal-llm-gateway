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
        env={
            **os.environ,
            "GROKBOT_LAUNCHER_SRC": str(
                _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"
            ),
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "GROKBOT_BOX_MCP_INSTALLER_EMBED" in proc.stdout
    assert "GROKBOT_BOX_MCP_LAUNCHER_EMBED" in proc.stdout
    assert "node_modules/mcp-remote/package.json" in proc.stdout


@pytest.mark.offline
def test_install_header_stdin_without_trailing_newline(tmp_path: Path) -> None:
    header = tmp_path / "vortex-mcp-headers"
    bearer = "d" * 24
    line = f"Authorization: Bearer {bearer}"
    proc = _run_install(
        tmp_path,
        "--header-stdin",
        stdin=line,
        header_dest=header,
    )
    assert proc.returncode == 0, proc.stderr
    assert header.read_text(encoding="utf-8") == f"Authorization: Bearer {bearer}\n"
    assert oct(header.stat().st_mode & 0o777) == oct(0o600)


@pytest.mark.offline
def test_install_refuse_replace_preserves_existing_bak(tmp_path: Path) -> None:
    header = tmp_path / "vortex-mcp-headers"
    first = "Authorization: Bearer " + "e" * 24 + "\n"
    second = "Authorization: Bearer " + "f" * 24 + "\n"
    third = "Authorization: Bearer " + "g" * 24 + "\n"
    header.write_text(first, encoding="utf-8")
    proc1 = _run_install(
        tmp_path,
        "--header-stdin",
        stdin=second,
        header_dest=header,
    )
    assert proc1.returncode == 2
    bak = header.with_name(header.name + ".bak")
    assert bak.read_text(encoding="utf-8") == first
    proc2 = _run_install(
        tmp_path,
        "--header-stdin",
        stdin=third,
        header_dest=header,
    )
    assert proc2.returncode == 2
    assert bak.read_text(encoding="utf-8") == first


@pytest.mark.offline
def test_print_paste_bootstrap_double_install_offline(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    header = home / ".gateway" / "vortex-mcp-headers"
    dummy = "z" * 24
    secret_line = f"Authorization: Bearer {dummy}"

    print_proc = subprocess.run(
        ["bash", str(_INSTALL), "--print"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT / "scripts"),
        env={
            **env,
            "GROKBOT_LAUNCHER_SRC": str(
                _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"
            ),
            "GROKBOT_HEADER_DEST": str(header),
        },
    )
    assert print_proc.returncode == 0, print_proc.stderr

    bootstrap = subprocess.run(
        ["bash", "-s"],
        input=print_proc.stdout,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert bootstrap.returncode == 0, bootstrap.stderr

    installer = home / ".local" / "bin" / "grokbot-box-mcp-install.sh"
    assert installer.is_file()
    assert oct(installer.stat().st_mode & 0o777) == oct(0o700)

    install_env = {
        **env,
        "GROKBOT_HEADER_DEST": str(header),
        "GROKBOT_LAUNCHER_SRC": str(
            home / ".local" / "bin" / "grokbot-mcp-remote-launcher.sh"
        ),
    }
    for _ in range(2):
        run = subprocess.run(
            ["bash", str(installer), "--header-stdin", "--skip-mcp-remote"],
            input=f"{secret_line}\n",
            capture_output=True,
            text=True,
            check=False,
            env=install_env,
        )
        assert run.returncode == 0, run.stderr + run.stdout
        assert dummy not in run.stdout
        assert dummy not in run.stderr
        assert header.is_file()
        assert oct(header.stat().st_mode & 0o777) == oct(0o600)
