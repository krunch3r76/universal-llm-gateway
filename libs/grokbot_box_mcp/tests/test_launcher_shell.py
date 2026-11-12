"""Shell launcher parity and failure-mode tests."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest
from grokbot_box_mcp.headers import BOX_HEADER_CANDIDATES

_REPO_ROOT = Path(__file__).resolve().parents[3]
_LAUNCHER = _REPO_ROOT / "scripts" / "grokbot-mcp-remote-launcher.sh"


def _hermetic_launcher(tmp_path: Path) -> Path:
    """Rewrite /home/box paths so launcher tests do not depend on a live sand."""
    fake_root = tmp_path / "boxroot"
    text = _LAUNCHER.read_text(encoding="utf-8")
    text = text.replace("/home/box", str(fake_root))
    dest = tmp_path / "grokbot-mcp-remote-launcher.sh"
    dest.write_text(text, encoding="utf-8")
    dest.chmod(0o755)
    return dest


def _parse_shell_candidates(text: str) -> list[str]:
    match = re.search(
        r"CANDIDATES=\(\s*\n((?:\s+/home/box/[^\n]+\n)+)\s*\)",
        text,
    )
    assert match, "CANDIDATES block not found in launcher shell"
    return [line.strip() for line in match.group(1).strip().splitlines()]


@pytest.mark.offline
def test_shell_header_candidates_match_python() -> None:
    text = _LAUNCHER.read_text(encoding="utf-8")
    shell = _parse_shell_candidates(text)
    assert tuple(shell) == BOX_HEADER_CANDIDATES


@pytest.mark.offline
def test_launcher_fails_without_header(tmp_path: Path) -> None:
    launcher = _hermetic_launcher(tmp_path)
    env = {"HOME": str(tmp_path)}
    proc = subprocess.run(
        ["bash", str(launcher), "https://mcp.k-1.me/mcp/code/"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "no MCP header file found" in proc.stderr


@pytest.mark.offline
def test_launcher_fails_without_pinned_mcp_remote(tmp_path: Path) -> None:
    launcher = _hermetic_launcher(tmp_path)
    fake_root = tmp_path / "boxroot"
    header = fake_root / ".gateway" / "vortex-mcp-headers"
    header.parent.mkdir(parents=True)
    header.write_text("Authorization: Bearer x\n", encoding="utf-8")
    env = {"HOME": str(tmp_path)}
    proc = subprocess.run(
        ["bash", str(launcher), "https://mcp.k-1.me/mcp/code/"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "not installed or wrong version" in proc.stderr


@pytest.mark.offline
def test_launcher_rejects_version_override(tmp_path: Path) -> None:
    env = {
        "HOME": str(tmp_path),
        "GROKBOT_MCP_REMOTE_VERSION": "9.9.9",
    }
    proc = subprocess.run(
        ["bash", str(_LAUNCHER), "https://mcp.k-1.me/mcp/code/"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert "GROKBOT_MCP_REMOTE_VERSION" in proc.stderr
