"""Unit tests for ResourceUpdateHandler state synchronization behavior."""

from unittest.mock import Mock

from _pytest.monkeypatch import MonkeyPatch

from gateway_websocket.handler.context import HandlerContext
from gateway_websocket.handler.system import ResourceUpdateHandler, logger


def _make_ctx(
    *,
    loaded_models: set[str] | None = None,
    model_details: dict[str, dict[str, object]] | None = None,
    measured_model_vram: dict[str, int] | None = None,
    catalog: dict[str, object] | None = None,
) -> HandlerContext:
    return HandlerContext(
        loaded_models=loaded_models or set(),
        loading_models=set(),
        busy_models=set(),
        models=set(),
        catalog=catalog or {},
        model_details=model_details or {},
        measured_model_vram=measured_model_vram or {},
    )


def test_authoritative_loaded_models_prunes_stale_measured_state() -> None:
    handler = ResourceUpdateHandler()
    ctx = _make_ctx(
        loaded_models={"model-a", "model-b"},
        measured_model_vram={"model-a": 1000, "model-b": 2000},
        model_details={
            "model-a": {"vram_usage": 1000, "ram_usage": 50},
            "model-b": {"vram_usage": 2000, "ram_usage": 60},
            "catalog-only-model": {"vram_usage": 9999, "ram_usage": 999},
        },
    )

    handler.handle({"loaded_models": ["model-a"]}, ctx)

    assert ctx.loaded_models == {"model-a"}
    assert ctx.measured_model_vram == {"model-a": 1000}
    assert "model-b" not in ctx.model_details
    assert "catalog-only-model" in ctx.model_details


def test_model_vram_only_does_not_mutate_loaded_models() -> None:
    handler = ResourceUpdateHandler()
    ctx = _make_ctx(loaded_models=set(), model_details={}, measured_model_vram={})

    handler.handle({"model_vram": {"model-new": 1234}}, ctx)

    assert ctx.loaded_models == set()
    assert ctx.measured_model_vram["model-new"] == 1234
    assert ctx.model_details["model-new"]["vram_usage"] == 1234
    assert ctx.model_details["model-new"]["ram_usage"] == 0


def test_catalog_structure_warning_once_per_handler_instance(
    monkeypatch: MonkeyPatch,
) -> None:
    handler = ResourceUpdateHandler()
    ctx = _make_ctx(catalog={})
    warning_spy = Mock()
    monkeypatch.setattr(logger, "warning", warning_spy)

    handler.handle({"model_vram": {"model-a": 100}}, ctx)
    handler.handle({"model_vram": {"model-a": 101}}, ctx)

    assert warning_spy.call_count == 1


def test_catalog_structure_warning_isolated_per_handler_instance(
    monkeypatch: MonkeyPatch,
) -> None:
    handler_one = ResourceUpdateHandler()
    handler_two = ResourceUpdateHandler()
    ctx_one = _make_ctx(catalog={})
    ctx_two = _make_ctx(catalog={})
    warning_spy = Mock()
    monkeypatch.setattr(logger, "warning", warning_spy)

    handler_one.handle({"model_vram": {"model-a": 100}}, ctx_one)
    handler_two.handle({"model_vram": {"model-b": 200}}, ctx_two)

    assert warning_spy.call_count == 2
