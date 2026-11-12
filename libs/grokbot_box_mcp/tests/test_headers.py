"""Header resolver and host sand-data seed tests."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest
from grokbot_box_mcp.headers import (
    BOX_HEADER_CANDIDATES,
    format_header_file_body,
    host_primary_header_path,
    resolve_box_header_path,
    write_host_header_seed,
)


@pytest.mark.offline
def test_resolve_prefers_first_existing_candidate() -> None:
    seen: dict[str, bool] = {
        BOX_HEADER_CANDIDATES[0]: False,
        BOX_HEADER_CANDIDATES[1]: True,
        BOX_HEADER_CANDIDATES[2]: True,
    }

    def exists(p: Path) -> bool:
        return seen.get(str(p), False)

    chosen, checked = resolve_box_header_path(exists=exists)
    assert chosen == BOX_HEADER_CANDIDATES[1]
    assert checked == list(BOX_HEADER_CANDIDATES[:2])


@pytest.mark.offline
def test_primary_host_header_is_operator_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    udd = home / ".gateway" / "grok-io-gc2"
    path = write_host_header_seed(udd, "tok", home=home, sand_fallback=False)
    assert path == home / ".gateway" / "vortex-mcp-headers"
    assert path.read_text(encoding="utf-8") == format_header_file_body("tok")


@pytest.mark.offline
def test_resolve_returns_none_with_full_audit_trail() -> None:
    chosen, checked = resolve_box_header_path(exists=lambda _p: False)
    assert chosen is None
    assert checked == list(BOX_HEADER_CANDIDATES)


@pytest.mark.offline
def test_write_host_header_seed_mode_and_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    udd = home / ".gateway" / "grok-io-gc2"
    path = write_host_header_seed(udd, "tok-abc", home=home, sand_fallback=False)
    assert path == host_primary_header_path(udd, home=home)
    assert path.read_text(encoding="utf-8") == format_header_file_body("tok-abc")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
