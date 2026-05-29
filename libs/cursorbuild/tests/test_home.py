"""Tests for cursorbuild.home — HOME isolation, copy-not-symlink, MCP filter.

The central property is that per-dispatch homes are mutually disjoint and
disjoint from the operator's real ``~/.cursor``: copying ``cli-config.json``
(rather than symlinking it) is what guarantees a dispatch can neither race nor
corrupt the real login on cursor's atomic-rename token refresh.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cursorbuild.home import (
    CursorbuildConfigError,
    dispatch_home_path,
    setup_dispatch_home,
)


def _fake_real_home(
    tmp_path: Path,
    *,
    with_auth: bool = True,
    with_mcp: bool = True,
    vortex: bool = True,
) -> Path:
    """Build a fake real HOME with a ``.cursor/`` config dir."""
    real = tmp_path / "real-home"
    cursor = real / ".cursor"
    cursor.mkdir(parents=True)
    if with_auth:
        (cursor / "cli-config.json").write_text(
            json.dumps({"login": "real-token"}), encoding="utf-8"
        )
    if with_mcp:
        servers: dict[str, object] = {
            "other-server": {"command": "other", "args": []},
        }
        if vortex:
            servers["vortex"] = {
                "command": "vortex-proxy",
                "args": ["--stdio"],
            }
        (cursor / "mcp.json").write_text(
            json.dumps({"mcpServers": servers}), encoding="utf-8"
        )
    return real


def test_dispatch_home_path_shape(tmp_path: Path) -> None:
    sidecar = tmp_path / "sidecars"
    p = dispatch_home_path("abc123", sidecar)
    assert p == sidecar / "abc123-home"


def test_setup_creates_isolated_tree(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    sidecar = tmp_path / "sidecars"
    home = setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=True)
    cursor = home / ".cursor"
    assert (cursor / "cli-config.json").exists()
    assert (cursor / "mcp.json").exists()


def test_auth_is_copied_not_symlinked(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    sidecar = tmp_path / "sidecars"
    home = setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=False)
    auth = home / ".cursor" / "cli-config.json"
    assert auth.exists()
    assert not auth.is_symlink()


def test_homes_are_mutually_disjoint(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    sidecar = tmp_path / "sidecars"
    h1 = setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=False)
    h2 = setup_dispatch_home("d2", sidecar, real_home=str(real), mcp_enabled=False)
    real_cursor = real / ".cursor"

    assert h1 != h2
    assert h1.resolve() != h2.resolve()
    assert h1.resolve() != real_cursor.resolve()
    assert h2.resolve() != real_cursor.resolve()

    auth1 = h1 / ".cursor" / "cli-config.json"
    auth2 = h2 / ".cursor" / "cli-config.json"
    assert auth1 != auth2

    # Mutating one dispatch's login affects neither the other nor the real one.
    auth1.write_text(json.dumps({"login": "mutated"}), encoding="utf-8")
    assert json.loads(auth2.read_text())["login"] == "real-token"
    real_auth = real_cursor / "cli-config.json"
    assert json.loads(real_auth.read_text())["login"] == "real-token"


def test_mcp_json_contains_only_vortex(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    sidecar = tmp_path / "sidecars"
    home = setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=True)
    data = json.loads((home / ".cursor" / "mcp.json").read_text())
    servers = data["mcpServers"]
    assert set(servers) == {"vortex"}
    assert "other-server" not in servers


def test_mcp_requires_auth_present(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path, with_auth=False)
    sidecar = tmp_path / "sidecars"
    with pytest.raises(CursorbuildConfigError):
        setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=True)


def test_mcp_requires_mcp_json_present(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path, with_mcp=False)
    sidecar = tmp_path / "sidecars"
    with pytest.raises(CursorbuildConfigError):
        setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=True)


def test_mcp_requires_vortex_server(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path, vortex=False)
    sidecar = tmp_path / "sidecars"
    with pytest.raises(CursorbuildConfigError):
        setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=True)


def test_non_mcp_copies_auth_and_writes_no_mcp_json(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path)
    sidecar = tmp_path / "sidecars"
    home = setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=False)
    cursor = home / ".cursor"
    assert (cursor / "cli-config.json").exists()
    assert not (cursor / "mcp.json").exists()


def test_non_mcp_missing_auth_does_not_raise(tmp_path: Path) -> None:
    real = _fake_real_home(tmp_path, with_auth=False)
    sidecar = tmp_path / "sidecars"
    home = setup_dispatch_home("d1", sidecar, real_home=str(real), mcp_enabled=False)
    assert (home / ".cursor").is_dir()
    assert not (home / ".cursor" / "cli-config.json").exists()
