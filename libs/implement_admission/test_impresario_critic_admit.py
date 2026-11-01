"""Role-less impresario critic admit helpers."""

from __future__ import annotations

import pytest

from implement_admission.impresario_critic_admit import (
    cloud_impresario_critic_models,
    is_roleless_impresario_critic_generate,
    resolve_roleless_impresario_critic_api_role,
)
from pipelines.team_dispatch_admit.v1.handlers.test_handlers import run_full_admit

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


@pytest.mark.parametrize("model", cloud_impresario_critic_models())
def test_every_cloud_allowed_critic_admits_roleless_code_review(model: str) -> None:
    assert is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        "code-review",
        model,
    )
    assert resolve_roleless_impresario_critic_api_role(model) == "synthesizer"
    plan = run_full_admit(
        {
            "op": "generate",
            "model": model,
            "job": "code-review",
            "request_id": f"cloud-critic-{model.replace('/', '-')}",
        }
    )
    assert plan.get("admitted") is True, plan
    assert plan.get("backend") == "api_role"
    assert plan.get("role") == "synthesizer"
    assert plan.get("model") == model


def test_sonnet_critic_resolves_synthesizer() -> None:
    assert (
        resolve_roleless_impresario_critic_api_role("anthropic/claude-sonnet-5-5")
        == "synthesizer"
    )


def test_unknown_critic_model_raises() -> None:
    with pytest.raises(ValueError, match="not a cloud impresario critic"):
        resolve_roleless_impresario_critic_api_role("anthropic/claude-opus-5")
