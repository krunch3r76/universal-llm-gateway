"""Tests for require_decision_asserted preflight stub."""

from __future__ import annotations

import pytest

from implement_admission.preflight import (
    DecisionNotAssertedError,
    require_decision_asserted,
)


class _CortexNoAssertion:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {"id": entity_id, "assertions": []}


class _CortexBelievedOnly:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "assertions": [{"id": 1, "superseded_by": None, "confidence": "believed"}],
        }


class _CortexConfirmed:
    def entity_get(self, entity_id: str, **kwargs):  # noqa: ANN003, ARG002
        return {
            "id": entity_id,
            "assertions": [{"id": 2, "superseded_by": None, "confidence": "confirmed"}],
        }


def test_require_decision_asserted_fails_when_missing() -> None:
    with pytest.raises(DecisionNotAssertedError):
        require_decision_asserted(cortex=_CortexNoAssertion())


def test_require_decision_asserted_fails_when_only_believed() -> None:
    with pytest.raises(DecisionNotAssertedError):
        require_decision_asserted(cortex=_CortexBelievedOnly())


def test_require_decision_asserted_passes_with_confirmed() -> None:
    require_decision_asserted(cortex=_CortexConfirmed())
