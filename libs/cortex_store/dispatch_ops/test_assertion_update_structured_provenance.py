"""`assertion_update` must reach the structured provenance columns.

`evidence_uris` and `reasoning_summary` are real columns on `assertions`, and
the auditor gate in ``cortex_store.assertion_quality`` reads `evidence_uris`
directly to decide whether a `confidence:confirmed` row is auditable. Before
this fix the dispatch op swallowed both into ``**_`` and, when they were the
only arguments supplied, answered ``"No fields to update"`` — so evidence
supplied by a caller was invisible to the gate that asks for it.
"""

from __future__ import annotations

import inspect

import pytest

from cortex_store.dispatch_ops.ops_assertions_update import _op_assertion_update
from cortex_store.models import AssertionUpdate
from cortex_store.routes.assertions._update import (
    _PATCHABLE_COLS,
    _PATCHABLE_JSON_COLS,
)

pytestmark = pytest.mark.offline

_STRUCTURED = ("evidence_uris", "reasoning_summary")


@pytest.mark.parametrize("field", _STRUCTURED)
def test_dispatch_op_accepts_field_by_name(field: str) -> None:
    """The op declares the parameter rather than absorbing it into **kwargs."""
    params = inspect.signature(_op_assertion_update).parameters
    assert field in params, f"{field} falls through to **_ and is discarded"


@pytest.mark.parametrize("field", _STRUCTURED)
def test_route_model_accepts_field(field: str) -> None:
    assert field in AssertionUpdate.model_fields


@pytest.mark.parametrize("field", _STRUCTURED)
def test_field_is_patchable_at_the_route(field: str) -> None:
    assert field in _PATCHABLE_COLS


def test_evidence_uris_is_json_encoded_on_write() -> None:
    """evidence_uris is JSON TEXT in SQLite; the SET value must be encoded."""
    assert "evidence_uris" in _PATCHABLE_JSON_COLS
    assert "reasoning_summary" not in _PATCHABLE_JSON_COLS


@pytest.mark.parametrize("field", _STRUCTURED)
def test_field_alone_is_not_no_fields_to_update(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression itself: either field alone must reach the impl."""
    seen: dict[str, object] = {}

    def _fake_impl(assertion_id: int, body: dict[str, object]) -> dict[str, object]:
        seen.update(body)
        return {"item": {"id": assertion_id}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_update._update_assertion_impl",
        _fake_impl,
    )
    value = ["cortex://notes/x.md"] if field == "evidence_uris" else "because"
    result = _op_assertion_update(assertion_id=1, **{field: value})

    assert result.get("error") != "No fields to update"
    assert seen.get(field) == value


def test_bare_string_evidence_uri_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A lone URI sent as a string is accepted, matching the `assert` op."""
    seen: dict[str, object] = {}

    def _fake_impl(assertion_id: int, body: dict[str, object]) -> dict[str, object]:
        seen.update(body)
        return {"item": {"id": assertion_id}}

    monkeypatch.setattr(
        "cortex_store.dispatch_ops.ops_assertions_update._update_assertion_impl",
        _fake_impl,
    )
    _op_assertion_update(assertion_id=1, evidence_uris="cortex://notes/x.md")
    assert seen.get("evidence_uris") == ["cortex://notes/x.md"]
