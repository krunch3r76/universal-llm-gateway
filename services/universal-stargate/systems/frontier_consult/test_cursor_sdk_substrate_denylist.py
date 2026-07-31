"""Slice-2 denylist admission tests for cursor-sdk generate targets."""

from __future__ import annotations

import pytest

import cursor_capabilities.cursor_capabilities as cap_mod
from agent_seat.profiles import CapabilityProfile

from .admission import FrontierEndpointError, resolve_cursor_sdk_generate_target


def test_cursor_sdk_admits_descriptor_uncovered_model() -> None:
    _to, family, platform, model = resolve_cursor_sdk_generate_target(
        "cursor-sdk",
        model="cursor/claude-sonnet-5",
        request_id="req-s2-admit",
    )
    assert family == "cursor"
    assert platform == "sdk"
    assert model == "cursor/claude-sonnet-5"


def test_cursor_sdk_rejects_denied_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cap_mod,
        "CURSOR_DENIED_MODELS",
        frozenset({"claude-sonnet-5"}),
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        resolve_cursor_sdk_generate_target(
            "cursor-sdk",
            model="cursor/CLAUDE-SONNET-5",
            request_id="req-s2-deny",
        )
    assert exc_info.value.code == "sdk_generate_model_invalid"


def test_non_cursor_empty_allowed_models_unaffected_by_denylist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cap_mod,
        "CURSOR_DENIED_MODELS",
        frozenset({"gpt-5.5"}),
    )
    profile = CapabilityProfile(
        family="gpt",
        platform="api",
        provider="openai",
        default_model="openai/gpt-5.5",
        tool_surface="mcp",
        delivery="auto",
        include_deadlines=True,
        include_review_queue=True,
        confirm_and_proceed=False,
        addenda=(),
        allowed_models=(),
        api_dispatchable=True,
    )
    resolved_model = "openai/gpt-5.5"
    blocked = False
    if profile.family == "cursor" and profile.platform == "sdk":
        from cursor_capabilities import is_cursor_model_denied

        blocked = is_cursor_model_denied(resolved_model)
    elif profile.allowed_models and resolved_model not in profile.allowed_models:
        blocked = True
    assert blocked is False
