"""grokbot-box-mcp-install.sh stdin / TTY validation (temp HOME)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTALL = _REPO_ROOT / "scripts" / "grokbot-box-mcp-install.sh"


def _stub_proxyshim_undici(shim_dir: Path) -> None:
    undici = shim_dir / "node_modules" / "undici"
    undici.mkdir(parents=True, exist_ok=True)
    (undici / "package.json").write_text('{"name":"undici","version":"6.29.0"}', encoding="utf-8")


def _run_install(
    tmp_path: Path,
    *extra_args: str,
    stdin: str | None = None,
    header_dest: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    shim_dir = tmp_path / ".proxyshim"
    _stub_proxyshim_undici(shim_dir)
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GROKBOT_PROXYSHIM_DIR": str(shim_dir),
        "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "preload.cjs"
        ),
        "GROKBOT_PROXYSHIM_PKG_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "package.json"
        ),
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
    bak = header.parent / "vortex-mcp-headers.bak"
    assert bak.is_file()
    assert oct(bak.stat().st_mode & 0o777) == oct(0o600)


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
    wrote = (
        f"grokbot-box-mcp-install: wrote {home}/.local/bin/grokbot-box-mcp-install.sh"
    )
    assert wrote in bootstrap.stderr, bootstrap.stderr

    installer = home / ".local" / "bin" / "grokbot-box-mcp-install.sh"
    launcher = home / ".local" / "bin" / "grokbot-mcp-remote-launcher.sh"
    assert installer.is_file()
    assert oct(installer.stat().st_mode & 0o777) == oct(0o700)
    assert launcher.is_file()
    assert oct(launcher.stat().st_mode & 0o777) == oct(0o755)

    shim_dir = home / ".proxyshim"
    _stub_proxyshim_undici(shim_dir)
    install_env = {
        **env,
        "GROKBOT_HEADER_DEST": str(header),
        "GROKBOT_LAUNCHER_SRC": str(
            home / ".local" / "bin" / "grokbot-mcp-remote-launcher.sh"
        ),
        "GROKBOT_PROXYSHIM_DIR": str(shim_dir),
        "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "preload.cjs"
        ),
        "GROKBOT_PROXYSHIM_PKG_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "package.json"
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


@pytest.mark.offline
def test_print_paste_blob_does_not_leak_errexit_to_interactive_shell(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
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
        },
    )
    assert print_proc.returncode == 0, print_proc.stderr
    assert "(\nset -euo pipefail" in print_proc.stdout

    refuse_input = (
        f"{print_proc.stdout}\n"
        "printf '%s\\n' 'Authorization: Bearer <token>' | "
        'bash "$HOME/.local/bin/grokbot-box-mcp-install.sh" --header-stdin --skip-mcp-remote\n'
        "false\n"
        "echo ALIVE\n"
        "echo SHELLFLAGS:$-\n"
    )
    shell = subprocess.run(
        ["bash", "--norc", "-i"],
        input=refuse_input,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=120,
    )
    assert "ALIVE" in shell.stdout, shell.stdout + shell.stderr
    flags = ""
    for line in shell.stdout.splitlines():
        if line.startswith("SHELLFLAGS:"):
            flags = line.split(":", 1)[1]
    assert "e" not in flags, flags


@pytest.mark.offline
def test_header_write_exit_trap_cleans_temp_on_mv_failure(tmp_path: Path) -> None:
    header = tmp_path / "hdr" / "vortex-mcp-headers"
    header.parent.mkdir()
    bearer_old = "h" * 24
    bearer_new = "i" * 24
    header.write_text(f"Authorization: Bearer {bearer_old}\n", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "mv").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    (fake_bin / "mv").chmod(0o755)
    shim_dir = tmp_path / ".proxyshim"
    _stub_proxyshim_undici(shim_dir)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "GROKBOT_HEADER_DEST": str(header),
        "GROKBOT_PROXYSHIM_DIR": str(shim_dir),
        "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "preload.cjs"
        ),
        "GROKBOT_PROXYSHIM_PKG_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "package.json"
        ),
    }
    proc = subprocess.run(
        [
            "bash",
            str(_INSTALL),
            "--header-stdin",
            "--replace",
            "--skip-mcp-remote",
        ],
        input=f"Authorization: Bearer {bearer_new}\n",
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, proc.stderr + proc.stdout
    leftovers = list(header.parent.glob(".vortex-mcp-headers.*"))
    assert leftovers == []


@pytest.mark.offline
def test_install_self_candidate_strips_login_shell_argv0(tmp_path: Path) -> None:
    """N11/N13: -bash argv0 must not break SELF resolution on the real install script."""
    shim_dir = tmp_path / ".proxyshim"
    _stub_proxyshim_undici(shim_dir)
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GROKBOT_PROXYSHIM_DIR": str(shim_dir),
        "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "preload.cjs"
        ),
        "GROKBOT_PROXYSHIM_PKG_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "package.json"
        ),
    }
    with _INSTALL.open("rb") as script:
        proc = subprocess.run(
            ["bash", "-c", "exec -a -bash bash -s -- --skip-mcp-remote"],
            stdin=script,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert "ok launcher=" in proc.stdout
    launcher = tmp_path / ".local" / "bin" / "grokbot-mcp-remote-launcher.sh"
    assert launcher.is_file()
    assert oct(launcher.stat().st_mode & 0o777) == oct(0o755)


@pytest.mark.offline
def test_print_paste_blob_bin_is_file_exits_nonzero_without_wrote(
    tmp_path: Path,
) -> None:
    """N12: bootstrap subshell failure must not emit a bogus wrote line or rc 0."""
    home = tmp_path / "home"
    home.mkdir()
    bin_path = home / ".local" / "bin"
    bin_path.parent.mkdir(parents=True)
    bin_path.write_text("not-a-directory\n", encoding="utf-8")
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
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
    assert bootstrap.returncode != 0, bootstrap.stderr + bootstrap.stdout
    assert "wrote" not in bootstrap.stderr
    assert "wrote" not in bootstrap.stdout


@pytest.mark.offline
def test_print_paste_blob_installer_path_is_directory_exits_nonzero_without_wrote(
    tmp_path: Path,
) -> None:
    """N15: subshell failure must not print wrote when INSTALLER path is a directory."""
    home = tmp_path / "home"
    home.mkdir()
    installer_as_dir = home / ".local" / "bin" / "grokbot-box-mcp-install.sh"
    installer_as_dir.parent.mkdir(parents=True)
    installer_as_dir.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
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
    assert bootstrap.returncode != 0, bootstrap.stderr + bootstrap.stdout
    assert "wrote" not in bootstrap.stderr
    assert "wrote" not in bootstrap.stdout
