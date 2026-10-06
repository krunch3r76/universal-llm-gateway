"""Engine-driven smoke tests (AC1 offline, AC2 split, checkpoint paging)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml
from maestro_induct.assemble import check_packet_sections

from .conftest import run_smoke

REPO = Path(__file__).resolve().parents[4]

@pytest.mark.asyncio
async def test_smoke_all_sections_present(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _content = await run_smoke(pipeline_executor)
    violations = check_packet_sections(packet, allowed_kinds=("scoreboard_missing",))
    assert violations == [], violations
    assert packet["meta"]["checkpoint_turn"] == 138
    assert packet["checkpoint"].get("next") is None or "sweep" in str(packet["checkpoint"].get("next", ""))
    assert smoke_router.unrouted == []


@pytest.mark.asyncio
async def test_smoke_open_consults_house_split(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    house_ids = {f"{c['thread']}#{c['turn']}" for c in packet["open_consults"]}
    outside = set(packet["open_consults_outside_house"])
    assert "12291#12" in house_ids
    assert "99999#3" in outside
    assert "12291#12" not in outside


@pytest.mark.asyncio
async def test_smoke_include_lanes_false(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    opts = {
        "root": "12286",
        "journal_entries": 2,
        "include_runbook": True,
        "include_lanes": False,
    }
    packet, _ = await run_smoke(pipeline_executor, options=opts)
    assert packet["lanes"] == []
    assert "lanes" in packet["meta"]["skipped"]
    assert packet["meta"]["lane_ids"]


@pytest.mark.asyncio
async def test_smoke_router_no_unrouted(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    await run_smoke(pipeline_executor)
    assert smoke_router.unrouted == []


@pytest.mark.asyncio
async def test_smoke_checkpoint_below_tip_window(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    assert packet["checkpoint"]["turn"] == 138
    assert "error" not in packet["checkpoint"]
    paths = [p for m, p, *_ in smoke_router.requests if m == "GET" and p == "/turns"]
    assert paths


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke_router", [{"top": 160}], indirect=True)
async def test_smoke_checkpoint_in_tip_window(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    assert packet["checkpoint"]["turn"] == 138
    after_pages = [
        r
        for m, p, params, _ in smoke_router.requests
        if m == "GET" and p == "/turns" and params and params.get("after_turn") is not None
    ]
    assert after_pages == []


@pytest.mark.asyncio
async def test_smoke_meta_timestamps(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root, monkeypatch
) -> None:
    import sys

    clients = sys.modules["_pipeline_handlers_maestro_induct_v1"]._clients
    t0 = 1_700_000_000.0
    seq = iter([t0, t0 + 0.01, t0 + 0.02, t0 + 0.03, t0 + 0.04, t0 + 0.05])
    monkeypatch.setattr(clients, "now_epoch", lambda: next(seq, t0 + 1.0))
    packet, _ = await run_smoke(pipeline_executor)
    assert packet["meta"]["started_at"] == "2023-11-14T22:13:20Z"
    assert packet["meta"]["generated_at"] >= packet["meta"]["started_at"]
    assert packet["meta"]["latency_ms"] >= 0


def test_yaml_step_types_match_register_handlers() -> None:
    yaml_path = REPO / "pipelines/maestro_induct/v1/maestro-induct-v1.yaml"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    yaml_types = {step["type"] for step in data["steps"]}
    recorded: list[str] = []

    class Rec:
        def register_domain_handler_class(self, domain, step_type, handler_class, *, external=False):
            recorded.append(step_type)

    import importlib.util

    init = yaml_path.parent / "handlers" / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "_pipeline_handlers_maestro_induct_v1",
        init,
        submodule_search_locations=[str(init.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    mod.register_handlers(Rec())
    assert set(recorded) == yaml_types
