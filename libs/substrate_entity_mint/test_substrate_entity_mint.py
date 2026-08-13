"""Shared substrate entity-mint lib — cortex entity_create relay."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from substrate_entity_mint.mint import ENTITY_CREATE_OPTIONAL, mint_entity

pytestmark = pytest.mark.offline


def test_mint_entity_posts_entity_create_dispatch() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": "service:probe", "type": "service", "name": "probe"}

    with patch("substrate_entity_mint.mint.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = mint_entity(id="service:probe", type="service", name="probe")

    assert result["id"] == "service:probe"
    payload = client.post.call_args.kwargs["json"]
    assert payload["tool"] == "entity_create"
    args = json.loads(payload["arguments"])
    assert args["id"] == "service:probe"
    assert args["type"] == "service"
    assert args["name"] == "probe"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("description", "owner for cursor-auto friction"),
        ("status", "unsubstantiated"),
        ("workflow_state", "open"),
        ("notes", "minted by V6 probe"),
        ("aliases", ["cursor-auto"]),
        ("attributes", {"density_triage": "mechanical"}),
        ("source_uri", "cortex://notes/system/specs/substrate-entity-mint.md"),
        ("content_hash", "abc123"),
    ],
)
def test_mint_entity_forwards_optional_field(field: str, value: object) -> None:
    assert field in ENTITY_CREATE_OPTIONAL
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": "service:probe"}

    with patch("substrate_entity_mint.mint.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        mint_entity(id="service:probe", type="service", name="probe", **{field: value})

    args = json.loads(client.post.call_args.kwargs["json"]["arguments"])
    assert args[field] == value


def test_mint_entity_omits_unset_optionals() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"id": "service:probe"}

    with patch("substrate_entity_mint.mint.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        mint_entity(id="service:probe", type="service", name="probe")

    args = json.loads(client.post.call_args.kwargs["json"]["arguments"])
    for field in ENTITY_CREATE_OPTIONAL:
        assert field not in args


def test_mint_entity_surfaces_http_409() -> None:
    response = MagicMock()
    response.status_code = 409
    response.text = "Entity already exists: service:probe"

    with patch("substrate_entity_mint.mint.make_sync_client") as client_factory:
        client = client_factory.return_value.__enter__.return_value
        client.post.return_value = response
        result = mint_entity(id="service:probe", type="service", name="probe")

    assert result["status_code"] == 409
    assert "error" in result
