"""MCP pre-validation for role-less impresario critic generate."""

from __future__ import annotations

import pytest
from implement_admission.impresario_critic_admit import (
    is_roleless_impresario_critic_generate,
)

pytestmark = pytest.mark.offline


@pytest.mark.parametrize("contract", ["code-review", "delivery-review", "check-review"])
def test_mcp_gate_admits_documented_critic_shape(contract: str) -> None:
    assert is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        contract,
        "anthropic/claude-sonnet-5-5",
    )


def test_mcp_gate_freeform_still_requires_role_seat() -> None:
    assert not is_roleless_impresario_critic_generate(
        "generate",
        None,
        None,
        "freeform",
        "anthropic/claude-sonnet-5-5",
    )
