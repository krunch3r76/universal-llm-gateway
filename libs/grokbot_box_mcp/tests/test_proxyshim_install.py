"""grokbot-box-mcp-install.sh proxyshim provisioning (temp roots only)."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_INSTALL = _REPO_ROOT / "scripts" / "grokbot-box-mcp-install.sh"
_PRELOAD_SRC = _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "preload.cjs"
_EXPECTED_PRELOAD_SHA = (
    "0e8a545a2b04946b489fad332f1af485d397f77c0c7fc161e2f6386ce016636c"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _stub_undici(shim_dir: Path) -> None:
    undici = shim_dir / "node_modules" / "undici"
    undici.mkdir(parents=True, exist_ok=True)
    pkg = undici / "package.json"
    if not pkg.is_file():
        pkg.write_text('{"name":"undici","version":"6.29.0"}', encoding="utf-8")
    index = undici / "index.js"
    if not index.is_file():
        index.write_text("module.exports = {};\n", encoding="utf-8")


_PROVISION = _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "provision.sh"


def _run_install(
    tmp_path: Path,
    *,
    proxyshim_dir: Path,
    extra_env: dict[str, str] | None = None,
    install_script: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    _stub_undici(proxyshim_dir)
    env = {
        "HOME": str(tmp_path),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "GROKBOT_PROXYSHIM_DIR": str(proxyshim_dir),
        "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(_PRELOAD_SRC),
        "GROKBOT_PROXYSHIM_PKG_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "package.json"
        ),
        "GROKBOT_LAUNCHER_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"
        ),
    }
    if extra_env:
        env.update(extra_env)
    script = install_script or _INSTALL
    return subprocess.run(
        ["bash", str(script), "--skip-mcp-remote"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT / "scripts"),
    )


@pytest.mark.offline
def test_install_provisions_proxyshim_with_expected_sha(tmp_path: Path) -> None:
    fake_box = tmp_path / "boxroot"
    shim_dir = fake_box / ".proxyshim"
    proc = _run_install(tmp_path, proxyshim_dir=shim_dir)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    preload = shim_dir / "preload.cjs"
    assert preload.is_file()
    assert _sha256(preload) == _EXPECTED_PRELOAD_SHA
    launcher = tmp_path / ".local" / "bin" / "grokbot-mcp-remote-launcher.sh"
    text = launcher.read_text(encoding="utf-8")
    assert "--require /home/box/.proxyshim/preload.cjs" in text
    assert preload.read_bytes() == _PRELOAD_SRC.read_bytes()


@pytest.mark.offline
def test_install_proxyshim_second_run_skips_unchanged(tmp_path: Path) -> None:
    fake_box = tmp_path / "boxroot"
    shim_dir = fake_box / ".proxyshim"
    proc1 = _run_install(tmp_path, proxyshim_dir=shim_dir)
    assert proc1.returncode == 0, proc1.stderr
    mtime1 = (shim_dir / "preload.cjs").stat().st_mtime_ns
    proc2 = _run_install(tmp_path, proxyshim_dir=shim_dir)
    assert proc2.returncode == 0, proc2.stderr
    assert "unchanged (skip" in proc2.stderr
    mtime2 = (shim_dir / "preload.cjs").stat().st_mtime_ns
    assert mtime2 == mtime1


@pytest.mark.offline
def test_install_missing_proxyshim_source_exits_before_write(tmp_path: Path) -> None:
    stripped = tmp_path / "provision.sh"
    body = _PROVISION.read_text(encoding="utf-8")
    body = body.replace(
        "grokbot_proxyshim_embed_usable() {",
        "grokbot_proxyshim_embed_usable() { return 1;",
        1,
    )
    stripped.write_text(body, encoding="utf-8")
    stripped.chmod(0o755)
    shim_dir = tmp_path / "empty-shim"
    proc = _run_install(
        tmp_path,
        proxyshim_dir=shim_dir,
        extra_env={
            "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(tmp_path / "missing" / "preload.cjs"),
            "GROKBOT_PROXYSHIM_PKG_SRC": str(tmp_path / "missing" / "package.json"),
            "GROKBOT_PROXYSHIM_PROVISION_SRC": str(stripped),
        },
    )
    assert proc.returncode == 1
    assert "missing proxyshim preload source" in proc.stderr
    assert not (shim_dir / "preload.cjs").exists()


@pytest.mark.offline
def test_install_print_includes_proxyshim_embed(tmp_path: Path) -> None:
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
    assert "GROKBOT_PROXYSHIM_PRELOAD_EMBED" in proc.stdout
    assert "setGlobalDispatcher" in proc.stdout
    assert "provision proxyshim" in proc.stderr or "provision proxyshim" in proc.stdout


@pytest.mark.offline
def test_empty_undici_package_json_triggers_stubbed_npm_install(tmp_path: Path) -> None:
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    (shim_dir / "package.json").write_text("{}", encoding="utf-8")
    undici = shim_dir / "node_modules" / "undici"
    undici.mkdir(parents=True, exist_ok=True)
    (undici / "package.json").write_text("{}", encoding="utf-8")
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    npm = fake_bin / "npm"
    npm.write_text(
        "#!/bin/sh\n"
        'mkdir -p "$PWD/node_modules/undici"\n'
        "printf '%s\\n' 'module.exports = {};' > \"$PWD/node_modules/undici/index.js\"\n"
        'printf \'%s\\n\' \'{"name":"undici","version":"6.29.0"}\' > "$PWD/node_modules/undici/package.json"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    npm.chmod(0o755)
    env = {
        "HOME": str(tmp_path),
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "GROKBOT_PROXYSHIM_DIR": str(shim_dir),
        "GROKBOT_PROXYSHIM_PRELOAD_SRC": str(_PRELOAD_SRC),
        "GROKBOT_PROXYSHIM_PKG_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-proxyshim" / "package.json"
        ),
        "GROKBOT_LAUNCHER_SRC": str(
            _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"
        ),
    }
    proc = subprocess.run(
        ["bash", str(_INSTALL), "--skip-mcp-remote"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT / "scripts"),
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert (shim_dir / "node_modules" / "undici" / "index.js").is_file()


@pytest.mark.offline
def test_preload_sha_mismatch_keeps_existing_dest_bytes(tmp_path: Path) -> None:
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    preload = shim_dir / "preload.cjs"
    preload.write_bytes(b"// prior contents kept on verify failure\n")
    prior = preload.read_bytes()
    _stub_undici(shim_dir)
    bad_src = tmp_path / "bad-preload.cjs"
    bad_src.write_text("// not the pinned preload\n", encoding="utf-8")
    proc = _run_install(
        tmp_path,
        proxyshim_dir=shim_dir,
        extra_env={"GROKBOT_PROXYSHIM_PRELOAD_SRC": str(bad_src)},
    )
    assert proc.returncode == 1, proc.stderr + proc.stdout
    assert "sha mismatch" in proc.stderr
    assert preload.read_bytes() == prior


@pytest.mark.offline
def test_preload_symlink_replaced_with_regular_file(tmp_path: Path) -> None:
    shim_dir = tmp_path / "shim"
    shim_dir.mkdir()
    target = shim_dir / "other.cjs"
    target.write_bytes(b"// stale\n")
    preload = shim_dir / "preload.cjs"
    preload.symlink_to(target)
    _stub_undici(shim_dir)
    proc = _run_install(tmp_path, proxyshim_dir=shim_dir)
    assert proc.returncode == 0, proc.stderr + proc.stdout
    assert preload.is_file() and not preload.is_symlink()
    assert _sha256(preload) == _EXPECTED_PRELOAD_SHA
