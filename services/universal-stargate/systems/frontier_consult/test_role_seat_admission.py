"""Offline admission FOL for role≠seat vocabulary (AC2–AC4, AC8)."""

from __future__ import annotations

import pytest

from agent_seat.dispatch_role_catalog import auto_seats, generate_roles

from .admission import (
    FrontierEndpointError,
    enforce_generate_role_seat_exclusive,
    enforce_handoff_seat_not_auto,
    reject_role_cursor_sdk_on_generate,
    resolve_auto_seat_generate_target,
)

pytestmark = pytest.mark.offline


def test_auto_seats_roster() -> None:
    assert auto_seats() == ["cursor-sdk"]
    assert "cursor-sdk" not in generate_roles()


def test_role_or_seat_required() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        enforce_generate_role_seat_exclusive(None, None, request_id="t")
    assert exc.value.code == "role_or_seat_required"


def test_cursor_model_only_skips_role_seat_required() -> None:
    enforce_generate_role_seat_exclusive(
        None, None, request_id="t", model="cursor/claude-opus-5"
    )


def test_role_seat_exclusive() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        enforce_generate_role_seat_exclusive(
            "reviewer", "cursor-sdk", request_id="t"
        )
    assert exc.value.code == "role_seat_exclusive"


def test_role_is_not_a_seat() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        reject_role_cursor_sdk_on_generate("cursor-sdk", request_id="t")
    assert exc.value.code == "role_is_not_a_seat"
    assert "seat=" in exc.value.reason


def test_seat_not_manual_on_handoff() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        enforce_handoff_seat_not_auto("cursor-sdk", request_id="t")
    assert exc.value.code == "seat_not_manual"


def test_seat_path_admits_cursor_sdk() -> None:
    to_agent, family, platform, model = resolve_auto_seat_generate_target(
        "cursor-sdk", model=None, request_id="t"
    )
    assert to_agent == "cursor-sdk"
    assert family == "cursor"
    assert platform == "sdk"
    assert model.startswith("cursor/")


def test_cursor_model_only_generate_admission() -> None:
    from .admission import is_cursor_sdk_generate_admission

    assert (
        is_cursor_sdk_generate_admission(
            None,
            seat=None,
            model="cursor/claude-opus-5",
            request_id="t",
        )
        is True
    )
    assert (
        is_cursor_sdk_generate_admission(
            None,
            seat=None,
            model="openai/gpt-5.5",
            request_id="t",
        )
        is False
    )