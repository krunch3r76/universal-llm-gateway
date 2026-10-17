"""Test 4.29 — effective_context_per_slot via InitDataCache catalog summary."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.core.websocket.init_cache import InitDataCache

_CTX_MODEL = "qwen3-5-9b-q8-0-131072"


def test_4_29_init_cache_effective_context_per_slot() -> None:
    registry = MagicMock()
    registry.get_available_synthetic_model_ids.return_value = [_CTX_MODEL]
    registry.get_model_resources.return_value = {"vram_mb": 15_000, "ram_mb": 0}
    registry.get_model_config.return_value = {"info": {"input_schema": "messages"}}
    registry.get_model_loader_config.return_value = {"parallel_slots": 4}
    registry.get_model_max_tokens.return_value = 131_072

    cache = InitDataCache(
        registry,
        worker_controller=MagicMock(),
        event_bus=None,
        gateway_config=MagicMock(),
    )
    summary = cache._get_catalog_summary()
    row = summary["model_resources"][_CTX_MODEL]
    assert row["context_length"] == 131_072
    assert row["parallel_slots"] == 4
    assert row["effective_context_per_slot"] == 32_768


_IO_CATALOG_FIXTURE: dict = {
    "models": {
        "qwen3-5-9b-q8-0": {
            "info": {
                "format": "gguf",
                "enabled": True,
                "capabilities": {"limits": {"max_context_length": 262_144}},
            },
            "profiles": {
                "131072": {
                    "loader": {"parallel_slots": 4, "n_ctx": 131_072},
                    "resources": {"vram_mb": 15_000, "ram_mb": 0},
                }
            },
        }
    }
}


def test_4_29_catalog_io_profile_row_effective_context() -> None:
    """Io GPU profile row via ModelRegistry + InitDataCache (catalog-shaped fixture)."""
    from src.core.model_registry.registry.core import ModelRegistry

    registry = ModelRegistry(_IO_CATALOG_FIXTURE)
    registry._models_with_available_paths.add(_CTX_MODEL)
    assert _CTX_MODEL in registry.get_available_synthetic_model_ids()
    profile_loader = registry.get_model_loader_config(_CTX_MODEL)
    assert profile_loader is not None
    assert profile_loader.get("parallel_slots") == 4
    assert registry.get_model_max_tokens(_CTX_MODEL) == 131_072

    cache = InitDataCache(
        registry,
        worker_controller=MagicMock(),
        event_bus=None,
        gateway_config=MagicMock(),
    )
    row = cache._get_catalog_summary()["model_resources"][_CTX_MODEL]
    assert row["parallel_slots"] == 4
    assert row["effective_context_per_slot"] == 32_768
