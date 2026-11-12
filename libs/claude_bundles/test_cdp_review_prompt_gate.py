"""CDP review prompt structural gate (a:43747 D2)."""

from __future__ import annotations

import pytest

from claude_bundles.cdp_review_prompt_gate import (
    CdpStagingError,
    enforce_review_prompt_gate,
)

_MIN_REVIEW = "x" * 512


@pytest.mark.parametrize("contract", ["code-review", "delivery-review", "check-review"])
def test_closeout_channel_line_refused_on_each_review_class_contract(
    contract: str,
) -> None:
    body = f"{_MIN_REVIEW}\ncloseout_channel: agent-bus:23296\n"
    with pytest.raises(CdpStagingError) as exc:
        enforce_review_prompt_gate(
            contract=contract,
            prompt_text=body,
        )
    assert exc.value.code == "review_prompt_self_delivery_directive"


def test_closeout_channel_inside_fence_admitted() -> None:
    body = f"{_MIN_REVIEW}\n```\ncloseout_channel: bus\n```\n"
    enforce_review_prompt_gate(contract="code-review", prompt_text=body)


def test_closeout_channel_admitted_on_confer() -> None:
    body = "closeout_channel: agent-bus:1\n"
    enforce_review_prompt_gate(contract="confer", prompt_text=body)


def test_quoted_bus_code_admitted() -> None:
    body = (
        f"{_MIN_REVIEW}\n"
        "Use agent_bus send and fs write only in prose; the harness owns delivery.\n"
    )
    enforce_review_prompt_gate(contract="code-review", prompt_text=body)


def test_size_gate_still_skips_check_review() -> None:
    enforce_review_prompt_gate(contract="check-review", prompt_text="short")
    with pytest.raises(CdpStagingError) as exc:
        enforce_review_prompt_gate(contract="code-review", prompt_text="short")
    assert exc.value.code == "review_prompt_too_small"


def test_casefolded_code_review_contract_gets_size_gate() -> None:
    with pytest.raises(CdpStagingError) as exc:
        enforce_review_prompt_gate(contract="Code-Review", prompt_text="short")
    assert exc.value.code == "review_prompt_too_small"


def test_closeout_channel_indented_line_not_matched() -> None:
    body = f"{_MIN_REVIEW}\n    closeout_channel: agent-bus:1\n"
    enforce_review_prompt_gate(contract="code-review", prompt_text=body)


def test_tilde_fence_not_closed_by_backtick_fence() -> None:
    body = f"{_MIN_REVIEW}\n~~~\ncloseout_channel: bus\n```\n"
    enforce_review_prompt_gate(contract="code-review", prompt_text=body)
