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
