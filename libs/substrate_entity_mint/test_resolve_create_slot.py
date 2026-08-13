"""Unit tests for identity-slot alias resolution in substrate_entity_mint."""

from __future__ import annotations

from substrate_entity_mint import resolve_create_slot


def test_resolve_create_slot_alias_only() -> None:
    resolved, err = resolve_create_slot(
        alias="service:probe", primary_name="id", alias_name="entity_id"
    )
    assert err is None
    assert resolved == "service:probe"


def test_resolve_create_slot_conflict() -> None:
    resolved, err = resolve_create_slot(
        primary="service:a",
        alias="service:b",
        primary_name="id",
        alias_name="entity_id",
    )
    assert resolved is None
    assert err is not None
    assert "not both with different values" in err
