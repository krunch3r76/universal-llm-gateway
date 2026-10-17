"""Test 4.33 — embedding measurement reads per-profile ubatch_size."""

from __future__ import annotations

import pytest

from src.jobs.measurement.embedding_profile_loader import merged_profile_loader


def test_4_33_profile_ubatch_merged() -> None:
    entry = {
        "loader": {"embedding": True, "ubatch_size": 8192},
        "devices": {
            "gpu": {
                "profiles": {
                    "8192": {"loader": {"ubatch_size": 2048, "pooling": "last"}},
                }
            }
        },
    }
    merged = merged_profile_loader(entry, ctx=8192, mode="gpu")
    assert merged["ubatch_size"] == 2048
    assert merged["pooling"] == "last"
    assert merged["embedding"] is True


@pytest.mark.asyncio
async def test_4_33_run_layer_test_receives_profile_ubatch(monkeypatch) -> None:
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock

    from src.jobs.measurement.embedding_measurement import (
        measure_gguf_embedding_profiles,
    )
    from src.jobs.measurement.execution import SubprocessTracker
    from src.jobs.measurement.request import MeasureJobRequest

    captured: dict = {}

    async def fake_run_layer_test(*_args, **kwargs):
        captured.update(kwargs)
        return {"success": True, "vram_mb": 100, "ram_mb": 50}

    monkeypatch.setattr(
        "src.jobs.measurement.embedding_measurement.run_layer_test",
        fake_run_layer_test,
    )
    bus = MagicMock()
    bus.publish_nowait = AsyncMock()
    monkeypatch.setattr(
        "src.jobs.measurement.embedding_measurement.get_event_bus",
        lambda: bus,
    )
    request = MeasureJobRequest(
        model_id="test-embed",
        mode="gpu",
        gpu_index=0,
        training_context_length=8192,
        contexts=[8192],
    )
    entry = {
        "loader": {"embedding": True},
        "devices": {
            "gpu": {
                "profiles": {
                    "8192": {"loader": {"ubatch_size": 2048, "pooling": "last"}},
                }
            }
        },
    }
    await measure_gguf_embedding_profiles(
        request,
        Path("/tmp/model.gguf"),
        SubprocessTracker(),
        entry,
        lambda _msg: None,
    )
    assert captured.get("ubatch_size") == 2048


@pytest.mark.asyncio
async def test_4_33_helpers_writeback_merges_profile_loader(monkeypatch) -> None:

    from src.jobs.measurement.helpers import _write_per_file_catalog_results

    entry = {
        "schema": "llama-cpp",
        "devices": {
            "gpu": {
                "profiles": {
                    "8192": {
                        "loader": {
                            "cache_type_k": "f16",
                            "cache_type_v": "f16",
                        }
                    }
                }
            }
        },
        "loader": {"embedding": True, "ubatch_size": 9999},
    }
    upserted: dict = {}

    class _Loader:
        def get_model(self, _mid: str):
            return entry

    class _Mgr:
        def upsert_local_only(self, _mid, data):
            upserted["data"] = data

    monkeypatch.setattr(
        "src.core.catalog.get_catalog_loader",
        lambda: _Loader(),
    )
    monkeypatch.setattr(
        "src.core.catalog_manager.get_catalog_manager",
        lambda: _Mgr(),
    )
    await _write_per_file_catalog_results(
        "test-embed",
        "gpu",
        {"8192": {"success": True, "vram_mb": 1, "ram_mb": 1, "n_gpu_layers": -1}},
        lambda _m: None,
        {
            "profile_loader_updates": {
                "8192": {"ubatch_size": 2048, "pooling": "last"},
            }
        },
    )
    prof_loader = (
        upserted["data"]["devices"]["gpu"]["profiles"]["8192"]["loader"]
    )
    assert prof_loader["ubatch_size"] == 2048
    assert prof_loader["cache_type_k"] == "f16"
    assert prof_loader["cache_type_v"] == "f16"
    assert upserted["data"]["loader"]["ubatch_size"] == 9999
