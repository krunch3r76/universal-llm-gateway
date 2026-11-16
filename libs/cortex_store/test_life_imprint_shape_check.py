"""Shape-check accept/reject matrix for cortex.life/v1."""

from __future__ import annotations

import pytest

from cortex_store.life_imprint.registry import load_registry
from cortex_store.life_imprint.shape_check import shape_check_patch

_REGISTRY = None


def _reg():
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = load_registry()
    return _REGISTRY


def _patch(graph: list[dict]) -> dict:
    return {"@context": "cortex.life/v1", "@graph": graph}


def test_accepts_typing_statement() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:new", "@type": "todo", "name": "Ship"}]),
        _reg(),
    )
    assert rejects == []


def test_rejects_unknown_predicate() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", "remember": "text"}]),
        _reg(),
    )
    assert any(r.code == "unknown_predicate" for r in rejects)


@pytest.mark.parametrize(
    "op",
    [
        "entity_merge",
        "entity_rekey",
        "relationship_delete",
        "session_close",
        "delegate",
        "dispatch",
    ],
)
def test_rejects_refuse_list_ops(op: str) -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", op: {"@id": "todo:y"}}]),
        _reg(),
    )
    assert any(r.code == "refused_op" for r in rejects)


def test_rejects_bad_priority_enum() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", "priority": "urgent"}]),
        _reg(),
    )
    assert any(r.code == "literal_type" for r in rejects)


def test_rejects_bad_due_date() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", "due": "next week"}]),
        _reg(),
    )
    assert any(r.code == "literal_type" for r in rejects)


def test_rejects_domain_mismatch_issued_by() -> None:
    rejects = shape_check_patch(
        _patch(
            [
                {
                    "@id": "person:alice",
                    "issued_by": {"@id": "organization:acme"},
                }
            ]
        ),
        _reg(),
    )
    assert any(r.code == "domain_mismatch" for r in rejects)


def test_rejects_wrong_context() -> None:
    rejects = shape_check_patch(
        {"@context": "other/vocab", "@graph": [{"@id": "todo:x", "@type": "todo"}]},
        _reg(),
    )
    assert rejects and rejects[0].code == "unknown_predicate"


def test_accepts_document_issued_by() -> None:
    rejects = shape_check_patch(
        _patch(
            [
                {
                    "@id": "document:brief",
                    "issued_by": {"@id": "organization:acme"},
                }
            ]
        ),
        _reg(),
    )
    assert rejects == []


def test_rejects_modifier_without_noted() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", "derivation_type": "user_statement"}]),
        _reg(),
    )
    assert any("noted modifiers" in r.detail for r in rejects)


def test_rejects_invalid_derivation_type_on_noted() -> None:
    rejects = shape_check_patch(
        _patch(
            [{"@id": "todo:x", "noted": "claim", "derivation_type": "quotation"}]
        ),
        _reg(),
    )
    assert any(r.code == "literal_type" for r in rejects)


def test_rejects_session_id_as_unknown_predicate() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", "noted": "claim", "session_id": "cursor-sdk-2026"}]),
        _reg(),
    )
    assert any(r.code == "unknown_predicate" for r in rejects)


def test_rejects_bad_valid_from_on_noted() -> None:
    rejects = shape_check_patch(
        _patch([{"@id": "todo:x", "noted": "claim", "valid_from": "next week"}]),
        _reg(),
    )
    assert any(r.code == "literal_type" for r in rejects)


def test_accepts_account_issued_by() -> None:
    rejects = shape_check_patch(
        _patch(
            [
                {
                    "@id": "account:chk",
                    "issued_by": {"@id": "organization:acme"},
                }
            ]
        ),
        _reg(),
    )
    assert rejects == []


def test_rejects_issued_by_range_mismatch_non_organization() -> None:
    rejects = shape_check_patch(
        _patch(
            [
                {
                    "@id": "document:brief",
                    "issued_by": {"@id": "person:alice"},
                }
            ]
        ),
        _reg(),
    )
    assert any(r.code == "range_mismatch" for r in rejects)


@pytest.mark.parametrize(
    ("graph_stmt", "expected_code"),
    [
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "derivation_type": "bogus"},
            "literal_type",
            id="bogus_derivation_regression",
        ),
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "evidence_uris": []},
            "literal_type",
            id="evidence_uris_empty",
        ),
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "evidence_uris": "not-a-list"},
            "literal_type",
            id="evidence_uris_not_list",
        ),
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "confidence_score": 0.5},
            "unknown_predicate",
            id="confidence_score_unknown_predicate",
        ),
        pytest.param(
            {"@id": "todo:x", "derivation_type": "user_statement"},
            "cardinality",
            id="modifier_without_noted_regression",
        ),
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "derivation_type": "quotation"},
            "literal_type",
            id="quotation_derivation_regression",
        ),
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "session_id": "cursor-sdk-2026"},
            "unknown_predicate",
            id="session_id_regression",
        ),
        pytest.param(
            {"@id": "todo:x", "noted": "claim", "valid_from": "next week"},
            "literal_type",
            id="bad_valid_from_regression",
        ),
    ],
)
def test_ac1c_invalid_noted_modifiers_rejected(
    graph_stmt: dict, expected_code: str
) -> None:
    rejects = shape_check_patch(_patch([graph_stmt]), _reg())
    assert rejects
    assert any(r.code == expected_code for r in rejects)
