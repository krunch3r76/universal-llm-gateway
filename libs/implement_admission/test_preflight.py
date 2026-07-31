"""Tests for require_decision_asserted preflight stub."""

from __future__ import annotations

import pytest

from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)


class _CortexNoAssertion:
    def assertion_state(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "entity_id": entity_id,
            "ratified": False,
            "confirmed_count": 0,
            "latest_confirmed_assertion_id": None,
        }


class _CortexBelievedOnly:
    def assertion_state(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "entity_id": entity_id,
            "ratified": False,
            "confirmed_count": 0,
            "latest_confirmed_assertion_id": None,
        }


class _CortexConfirmed:
    def assertion_state(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "entity_id": entity_id,
            "ratified": True,
            "confirmed_count": 1,
            "latest_confirmed_assertion_id": 2,
        }


class _CortexStagedConfirmed:
    def assertion_state(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "entity_id": entity_id,
            "ratified": True,
            "confirmed_count": 1,
            "latest_confirmed_assertion_id": 11835,
        }


def test_require_decision_asserted_fails_when_missing() -> None:
    with pytest.raises(DecisionNotAssertedError):
        require_decision_asserted(cortex=_CortexNoAssertion())


def test_require_decision_asserted_fails_when_only_believed() -> None:
    with pytest.raises(DecisionNotAssertedError):
        require_decision_asserted(cortex=_CortexBelievedOnly())


def test_require_decision_asserted_passes_with_confirmed() -> None:
    require_decision_asserted(cortex=_CortexConfirmed())


def test_require_decision_asserted_passes_staged_confirmed() -> None:
    require_decision_asserted(cortex=_CortexStagedConfirmed())


def test_require_decision_asserted_fails_on_dispatch_error() -> None:
    class _CortexError:
        def assertion_state(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
            return {"error": "entity_id is required"}

    with pytest.raises(DecisionNotAssertedError, match="assertion_state failed"):
        require_decision_asserted(cortex=_CortexError())
