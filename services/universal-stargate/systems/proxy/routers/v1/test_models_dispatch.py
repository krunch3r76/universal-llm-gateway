"""A3 — stargate /v1/models dispatch projection (G11).

Covers the three seams added for the cloud-dispatch projection:
``get_model_dispatch_metadata`` (federated-only read of the per-model
``model_resources["dispatch"]`` carrier) and the ``dispatch`` attachment in
``_build_models_response`` / ``_build_model_entry``. Asserts cloud rows carry
the libs wire facet and dispatchless rows are unaffected (local-row additivity).
"""

from __future__ import annotations

from typing import Any

from llm_adapters.capability_dispatch import resolve, to_wire_dict
from model_id import ModelId

from systems.proxy.routers.v1.models import (
    _build_model_entry,
    _build_models_response,
)
from systems.routing.selection.catalog import get_model_dispatch_metadata

_OPUS = "anthropic/claude-opus-4-8"
_WIRE = to_wire_dict(resolve(_OPUS))


class _FakeGateway:
    def __init__(self, model_resources: dict[ModelId, dict[str, Any]]) -> None:
        self.model_resources = model_resources


class _FakeFederatedManager:
    def __init__(self, gateways: list[_FakeGateway]) -> None:
        self._gateways = gateways

    def get_healthy_gateways(self) -> list[_FakeGateway]:
        return self._gateways


def test_dispatch_metadata_reads_federated_dispatch() -> None:
    opus = ModelId.parse(_OPUS)
    other = ModelId.parse("anthropic/claude-3-haiku")
    fed = _FakeFederatedManager(
        [
            _FakeGateway(
                {
                    opus: {"max_concurrent_requests": 5, "dispatch": _WIRE},
                    other: {"max_concurrent_requests": 5},
                }
            )
        ]
    )
    meta = get_model_dispatch_metadata(None, fed)
    assert meta == {_OPUS: _WIRE}


def test_dispatch_metadata_empty_without_federation() -> None:
    assert get_model_dispatch_metadata(None, None) == {}


def test_models_response_attaches_dispatch_only_where_present() -> None:
    response = _build_models_response(
        [_OPUS, "local-model-8192"], [], None, {_OPUS: _WIRE}
    )
    rows = {r["id"]: r for r in response["data"]}
    assert rows[_OPUS]["dispatch"] == _WIRE
    assert rows[_OPUS]["type"] == "model"
    # Dispatchless (e.g. local) row is unchanged — additive only.
    assert "dispatch" not in rows["local-model-8192"]


def test_models_response_no_dispatch_metadata_omits_key() -> None:
    response = _build_models_response([_OPUS], [], None, None)
    assert "dispatch" not in response["data"][0]


def test_model_entry_attaches_dispatch_when_present() -> None:
    assert _build_model_entry(_OPUS, None, {_OPUS: _WIRE})["dispatch"] == _WIRE


def test_model_entry_omits_dispatch_when_absent() -> None:
    entry = _build_model_entry("local-model-8192", None, {_OPUS: _WIRE})
    assert "dispatch" not in entry
