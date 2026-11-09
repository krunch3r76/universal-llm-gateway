"""Policy loader for cursor_sdk_other_models_pool."""

from __future__ import annotations

import pytest

from implement_admission.cursor_sdk_other_models_pool import (
    load_cursor_sdk_other_models_pool_mode,
    other_models_pool_generate_admitted,
)

pytestmark = pytest.mark.offline


def test_default_mode_is_deny() -> None:
    assert load_cursor_sdk_other_models_pool_mode() == "deny"


@pytest.mark.parametrize(
    ("pin", "resolved", "admitted"),
    [
        (None, "cursor/composer-2.5", True),
        ("cursor/composer-2.5", "cursor/composer-2.5", True),
        (None, "cursor/claude-fable-5", False),
        ("cursor/claude-fable-5", "cursor/claude-fable-5", False),
    ],
)
def test_deny_mode(pin: str | None, resolved: str, admitted: bool) -> None:
    assert (
        other_models_pool_generate_admitted(model_pin=pin, resolved_model=resolved)
        is admitted
    )


def test_explicit_pin_only_mode_admits_pin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "implement_admission.cursor_sdk_other_models_pool."
        "load_cursor_sdk_other_models_pool_mode",
        lambda *_a, **_k: "explicit_pin_only",
    )
    assert (
        other_models_pool_generate_admitted(
            model_pin="cursor/claude-fable-5",
            resolved_model="cursor/claude-fable-5",
        )
        is True
    )
    assert (
        other_models_pool_generate_admitted(
            model_pin=None,
            resolved_model="cursor/claude-fable-5",
        )
        is False
    )
