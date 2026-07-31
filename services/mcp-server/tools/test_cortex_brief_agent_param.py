"""cortex_brief seat= resolution + mount-aware blank default."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_profile import bind_request
from tools.cortex_named_tools._orchestration_tools import _resolve_boot_family_platform


def test_seat_cursor_alias() -> None:
    assert _resolve_boot_family_platform(seat="cursor") == ("claude", "cursor")


def test_seat_claude_web_slug() -> None:
    assert _resolve_boot_family_platform(seat="claude-web") == ("claude", "web")


def test_seat_web_anthropic() -> None:
    assert _resolve_boot_family_platform(seat="web-anthropic") == ("claude", "web")


def test_seat_capability_cell_grok_api_multi() -> None:
    assert _resolve_boot_family_platform(seat="grok-api-multi") == (
        "grok",
        "api-multi",
    )


def test_blank_on_life_mount_defaults_web() -> None:
    with bind_request("default", surface="life"):
        assert _resolve_boot_family_platform() == ("claude", "web")


def test_blank_on_code_mount_defaults_cursor() -> None:
    with bind_request("default", surface="code"):
        assert _resolve_boot_family_platform() == ("claude", "cursor")


def test_blank_without_surface_requires_seat() -> None:
    result = _resolve_boot_family_platform()
    assert isinstance(result, dict)
    assert result["reason"] == "seat_required"


def test_blank_uses_registration_mount_surface() -> None:
    assert _resolve_boot_family_platform(mount_surface="code") == (
        "claude",
        "cursor",
    )
    assert _resolve_boot_family_platform(mount_surface="life") == ("claude", "web")


def test_family_kwarg_rejected() -> None:
    with pytest.raises(TypeError):
        _resolve_boot_family_platform(family="claude")  # type: ignore[call-arg]


def test_agent_kwarg_rejected() -> None:
    with pytest.raises(TypeError):
        _resolve_boot_family_platform(agent="cursor")  # type: ignore[call-arg]
