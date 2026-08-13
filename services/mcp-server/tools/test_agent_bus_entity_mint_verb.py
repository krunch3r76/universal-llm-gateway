"""Request-surface ``substrate_entity_mint`` verb — per-field fidelity and relay."""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest
from contract_vocab import CANONICAL_CONTRACTS
from substrate_entity_mint.mint import ENTITY_CREATE_OPTIONAL

from tools.agent_bus.entity_mint import _entity_mint_dispatch

_REQUIRED = dict(id="service:probe", type="service", name="probe")

_FORWARD_CASES = [
    ("description", "owner for cursor-auto friction"),
    ("status", "unsubstantiated"),
    ("workflow_state", "open"),
    ("notes", "minted by V6 probe"),
    ("aliases", ["cursor-auto"]),
    ("attributes", {"density_triage": "mechanical"}),
    ("source_uri", "cortex://notes/system/specs/substrate-entity-mint.md"),
    ("content_hash", "abc123"),
]

_REJECT_CASES = [
    ("retention_policy", "ephemeral", "entity_mint_retention_not_on_dispatch"),
    ("retention_ttl_days", 7, "entity_mint_retention_not_on_dispatch"),
    ("confidence_band", "confirmed", "entity_mint_trait_not_settable_at_create"),
    ("lifecycle", "active", "entity_mint_trait_not_settable_at_create"),
    ("adoption", "adopted", "entity_mint_trait_not_settable_at_create"),
    ("density_triage", "mechanical", "entity_mint_density_triage_is_attribute"),
]


def test_substrate_entity_mint_not_in_canonical_contracts() -> None:
    assert "substrate_entity_mint" not in CANONICAL_CONTRACTS


def test_entity_mint_dispatch_signature_rejects_hop_fields() -> None:
    params = inspect.signature(_entity_mint_dispatch).parameters
    assert "thread" not in params
    assert "continuity_hop" not in params
    assert "new_slug" not in params


def test_entity_mint_signature_accepts_every_dispatch_consumed_field() -> None:
    params = inspect.signature(_entity_mint_dispatch).parameters
    for field in ("id", "type", "name") + ENTITY_CREATE_OPTIONAL:
        assert field in params, f"silent-drop risk: {field} missing from signature"


@pytest.mark.parametrize("missing", ["id", "type", "name"])
def test_entity_mint_rejects_missing_required(missing: str) -> None:
    kwargs = dict(_REQUIRED)
    kwargs[missing] = ""
    result = _entity_mint_dispatch(**kwargs)
    assert result["reason"] == f"entity_mint_{missing}_required"
    assert result["status_code"] == 422


@pytest.mark.parametrize("missing", ["id", "type", "name"])
def test_entity_mint_does_not_post_when_required_missing(missing: str) -> None:
    kwargs = dict(_REQUIRED)
    kwargs[missing] = ""
    with patch("tools.agent_bus.entity_mint.mint_entity") as minted:
        _entity_mint_dispatch(**kwargs)
    minted.assert_not_called()


@pytest.mark.parametrize(("field", "value"), _FORWARD_CASES)
def test_entity_mint_forwards_optional_field(field: str, value: object) -> None:
    """Each entity_create optional is forwarded verbatim — not dropped in aggregate."""
    with patch(
        "tools.agent_bus.entity_mint.mint_entity",
        return_value={"id": "service:probe"},
    ) as minted:
        result = _entity_mint_dispatch(**_REQUIRED, **{field: value})
    assert minted.call_args.kwargs[field] == value
    assert result["entity_id"] == "service:probe"


@pytest.mark.parametrize(("field", "value", "reason"), _REJECT_CASES)
def test_entity_mint_rejects_named_field(
    field: str, value: object, reason: str
) -> None:
    """Each non-forwarded entity_create-adjacent field is a named 422, not a drop."""
    with patch("tools.agent_bus.entity_mint.mint_entity") as minted:
        result = _entity_mint_dispatch(**_REQUIRED, **{field: value})
    minted.assert_not_called()
    assert result["reason"] == reason
    assert result["status_code"] == 422


def test_entity_mint_accepts_identity_aliases() -> None:
    with patch(
        "tools.agent_bus.entity_mint.mint_entity",
        return_value={"id": "service:probe"},
    ) as minted:
        result = _entity_mint_dispatch(
            entity_id="service:probe",
            entity_type="service",
            title="probe",
        )
    minted.assert_called_once()
    assert minted.call_args.kwargs["id"] == "service:probe"
    assert minted.call_args.kwargs["type"] == "service"
    assert minted.call_args.kwargs["name"] == "probe"
    assert result["entity_id"] == "service:probe"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({**_REQUIRED, "entity_id": "service:other"}, "entity_mint_id_alias_conflict"),
        ({**_REQUIRED, "entity_type": "todo"}, "entity_mint_type_alias_conflict"),
        ({**_REQUIRED, "title": "other"}, "entity_mint_name_alias_conflict"),
    ],
)
def test_entity_mint_rejects_conflicting_aliases(
    kwargs: dict[str, object], reason: str
) -> None:
    with patch("tools.agent_bus.entity_mint.mint_entity") as minted:
        result = _entity_mint_dispatch(**kwargs)
    minted.assert_not_called()
    assert result["reason"] == reason
    assert result["status_code"] == 422


def test_entity_mint_passes_through_409() -> None:
    with patch(
        "tools.agent_bus.entity_mint.mint_entity",
        return_value={
            "error": "Entity already exists: service:probe",
            "status_code": 409,
        },
    ):
        result = _entity_mint_dispatch(**_REQUIRED)
    assert result["status_code"] == 409
    assert "error" in result
