"""Pin equality for code-contract admits. Mismatch aborts; Composer match does not."""

from __future__ import annotations

import pytest

from .admission import FrontierEndpointError
from .cursor_sdk_model_pin import (
    append_resolved_model_fact,
    models_identify,
    refuse_code_contract_model_mismatch,
)
from .events import FrontierSdkWorkerDispatched


def test_prefix_and_bare_composer_identify() -> None:
    assert models_identify("cursor/composer-2.5", "composer-2.5")


def test_mismatch_raises_and_match_does_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "systems.frontier_consult.cursor_sdk_model_pin.emit_sdk_model_mismatch",
        lambda **_k: None,
    )
    with pytest.raises(FrontierEndpointError) as exc_info:
        refuse_code_contract_model_mismatch(
            request_id="req-mismatch",
            contract="freeform",
            requested_model="cursor/grok-4.7",
            resolved_model="cursor/composer-2.5",
        )
    assert exc_info.value.code == "resolved_model_mismatch"
    refuse_code_contract_model_mismatch(
        request_id="req-match",
        contract="implement",
        requested_model="cursor/composer-2.5",
        resolved_model="cursor/composer-2.5",
    )


def test_omitted_pin_is_not_a_substitution() -> None:
    refuse_code_contract_model_mismatch(
        request_id="req-omit",
        contract="implement",
        requested_model=None,
        resolved_model="cursor/composer-2.5",
    )


def test_admit_fact_quotes_resolved_model() -> None:
    body = append_resolved_model_fact(
        "SDK freeform generate dispatch.",
        "cursor/grok-4.7",
    )
    assert "resolved_model: cursor/grok-4.7" in body
    event = FrontierSdkWorkerDispatched(
        request_id="req",
        thread_id="1",
        execution_id="exec",
        resolved_model="cursor/composer-2.5",
    )
    assert event.payload["resolved_model"] == "cursor/composer-2.5"
