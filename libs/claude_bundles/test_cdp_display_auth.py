"""Unit tests for CDP display XAUTHORITY resolution (a:32225)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_display_auth import (
    DisplayAuthError,
    apply_display_auth_env,
    display_digit,
    flat_auth_path,
    per_display_auth_path,
    resolve_display_auth,
)

pytestmark = pytest.mark.offline


def test_display_digit_rejects_garbage() -> None:
    with pytest.raises(DisplayAuthError):
        display_digit("")
    with pytest.raises(DisplayAuthError):
        display_digit(":")
    with pytest.raises(DisplayAuthError):
        display_digit("abc")


def test_resolve_prefers_per_display(tmp_path: Path) -> None:
    per = per_display_auth_path(":2", home=tmp_path)
    per.parent.mkdir(parents=True)
    per.write_bytes(b"per")
    flat = flat_auth_path(home=tmp_path)
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_bytes(b"flat")
    auth = resolve_display_auth(":2", home=tmp_path, discover=False)
    assert auth.source == "per_display"
    assert auth.path == per


def test_resolve_falls_to_flat(tmp_path: Path) -> None:
    flat = flat_auth_path(home=tmp_path)
    flat.parent.mkdir(parents=True)
    flat.write_bytes(b"flat")
    auth = resolve_display_auth(":2", home=tmp_path, discover=False)
    assert auth.source == "flat"
    assert auth.path == flat


def test_apply_strips_inherited_xauthority(tmp_path: Path) -> None:
    flat = flat_auth_path(home=tmp_path)
    flat.parent.mkdir(parents=True)
    flat.write_bytes(b"flat")
    env = {"XAUTHORITY": "/wrong/cookie", "DISPLAY": ":9"}
    auth = apply_display_auth_env(env, ":2", home=tmp_path)
    assert env["XAUTHORITY"] == str(flat)
    assert env["DISPLAY"] == ":2"
    assert auth.source == "flat"


def test_apply_raises_when_unresolvable(tmp_path: Path) -> None:
    env: dict[str, str] = {"XAUTHORITY": "/wrong"}
    with pytest.raises(DisplayAuthError, match="no resolvable Xauthority"):
        apply_display_auth_env(env, ":2", home=tmp_path)
    assert "XAUTHORITY" not in env
