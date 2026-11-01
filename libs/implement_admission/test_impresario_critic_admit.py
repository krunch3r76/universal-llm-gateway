"""Role-less impresario critic admit helpers."""

from __future__ import annotations

import pytest

from implement_admission.impresario_critic_admit import (
    is_roleless_impresario_critic_generate,
    resolve_roleless_impresario_critic_api_role,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize("job", ["code-review", "delivery-review", "check-review"])
def test_roleless_cloud_critic_shape(job: str) -> None:
    assert is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        job,
        "anthropic/claude-sonnet-5-5",
    )


def test_roleless_freeform_not_critic_shape() -> None:
    assert not is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        "freeform",
        "anthropic/claude-sonnet-5-5",
    )


def test_cdp_critic_not_roleless_cloud() -> None:
    assert not is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        "code-review",
        "cdp/opus-5",
    )


def test_cursor_critic_not_roleless_cloud() -> None:
    assert not is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        "code-review",
        "cursor/composer-2.5",
    )


def test_role_present_not_roleless() -> None:
    assert not is_roleless_impresario_critic_generate(
        "generate",
        "synthesizer",
        None,
        "code-review",
        "anthropic/claude-sonnet-5-5",
    )


def test_sonnet_critic_resolves_synthesizer() -> None:
    assert (
        resolve_roleless_impresario_critic_api_role("anthropic/claude-sonnet-5-5")
        == "synthesizer"
    )


def test_unknown_critic_model_raises() -> None:
    with pytest.raises(ValueError, match="not a cloud impresario critic"):
        resolve_roleless_impresario_critic_api_role("anthropic/claude-opus-5")
