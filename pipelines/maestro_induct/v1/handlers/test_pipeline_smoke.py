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
        params
        for m, p, params, _ in smoke_router.requests
        if m == "GET" and p == "/turns" and params and params.get("after_turn") is not None
    ]
    assert after_pages == []


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke_router", [{"top": 10000}], indirect=True)
async def test_smoke_checkpoint_scan_cap(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, content = await run_smoke(pipeline_executor)
    err = packet["checkpoint"]["error"]
    assert err["kind"] == "checkpoint_scan_cap_reached"
    assert err["scanned_down_to"] == 1951
    pages = [
        params
        for m, p, params, _ in smoke_router.requests
        if m == "GET" and p == "/turns" and params and params.get("after_turn") is not None
    ]
    assert len(pages) == 8
    assert any(e.get("kind") == "checkpoint_scan_cap_reached" for e in packet["meta"]["errors"])
    assert len(content.encode("utf-8")) <= 12288


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke_router", [{"top": 600, "cps": {}}], indirect=True)
async def test_smoke_checkpoint_none_on_thread(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    err = packet["checkpoint"]["error"]
    assert err["kind"] == "checkpoint_not_found"
    assert err["scanned_down_to"] == 1
    turns = [
        (m, p, params)
        for m, p, params, _ in smoke_router.requests
        if m == "GET" and p == "/turns" and params and params.get("compact") == "true"
    ]
    assert len(turns) == 2
    assert turns[1][2].get("after_turn") == "0"
    assert turns[1][2].get("last") == "550"
    assert not any(p == "/turns/by-number" for _m, p, _params, _ in smoke_router.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "smoke_router",
    [{"top": 2571, "cps_turns": [138], "superseded": frozenset({138})}],
    indirect=True,
)
async def test_smoke_checkpoint_superseded_latest_found(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    assert packet["checkpoint"]["turn"] == 138
    for m, p, params, _ in smoke_router.requests:
        if m == "GET" and p == "/turns" and params and params.get("compact") == "true":
            assert params.get("include_superseded") == "true"


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke_router", [{"append_cp_after_tip": True}], indirect=True)
async def test_smoke_checkpoint_posted_mid_scan_unseen(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    assert packet["checkpoint"]["turn"] == 138


@pytest.mark.asyncio
@pytest.mark.parametrize("smoke_router", [{"drop": (600, 1600)}], indirect=True)
async def test_smoke_checkpoint_ladder_gaps(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    assert packet["checkpoint"]["turn"] == 138
    pages = [
        (params.get("after_turn"), params.get("last"))
        for m, p, params, _ in smoke_router.requests
        if m == "GET" and p == "/turns" and params and params.get("after_turn") is not None
    ]
    assert ("521", "1000") in pages
    assert pages[pages.index(("521", "1000")) + 1] == ("0", "521")


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


@pytest.mark.asyncio
async def test_smoke_files_root_unset_emits_with_errors(
    pipeline_executor, smoke_router, monkeypatch
) -> None:
    monkeypatch.delenv("CORTEX_FILES_ROOT", raising=False)
    packet, content = await run_smoke(pipeline_executor)
    for key in ("house", "continuity", "journal", "runbook"):
        assert packet[key]["error"]["kind"] == "files_root_unset"
    assert len(content.encode("utf-8")) <= 12288


@pytest.mark.asyncio
async def test_smoke_root_thread_consult_is_house(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    house_ids = {f"{c['thread']}#{c['turn']}" for c in packet["open_consults"]}
    outside = set(packet["open_consults_outside_house"])
    assert "12286#900" in house_ids
    assert "12286#900" not in outside


@pytest.mark.asyncio
async def test_smoke_lane_consult_outside_tail_window_is_house(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root
) -> None:
    packet, _ = await run_smoke(pipeline_executor)
    house_ids = {f"{c['thread']}#{c['turn']}" for c in packet["open_consults"]}
    outside = set(packet["open_consults_outside_house"])
    assert "12359#5" in house_ids
    assert "12359#5" not in outside
    scans = [
        params
        for m, p, params, _ in smoke_router.requests
        if m == "GET"
        and p == "/turns"
        and params
        and str(params.get("thread")) == "12359"
        and params.get("to") == "web-anthropic"
        and params.get("unread") == "true"
    ]
    assert scans
    assert "last" not in scans[0]


@pytest.mark.asyncio
async def test_smoke_deadline_exhausted_emits_with_errors(
    pipeline_executor, smoke_router, staged_cortex, cortex_files_root, monkeypatch
) -> None:
    import sys

    clients = sys.modules["_pipeline_handlers_maestro_induct_v1"]._clients
    t = [1000.0]

    def tick():
        t[0] += 3.0
        return t[0]

    monkeypatch.setattr(clients, "now_epoch", tick)
    packet, content = await run_smoke(pipeline_executor)
    assert any(e.get("kind") == "deadline_exceeded" for e in packet["meta"]["errors"])
    assert len(content.encode("utf-8")) <= 12288
    assert len(smoke_router.requests) < 30


def test_assemble_reserves_stamps_then_emit_near_cap(monkeypatch) -> None:
    import asyncio
    import importlib.util
    import json as json_mod
    import sys
    import types

    from maestro_induct.assemble import assemble_packet, packet_nbytes
    from maestro_induct.test_assemble import _minimal_sections

    em = "\u2014"
    lanes = [
        {"thread": str(i), "source": "bus_tail", "tail": [{"turn": 1, "subject": "z" * 200}]}
        for i in range(40)
    ]
    base_consult = {
        "thread": "12291",
        "turn": 12,
        "after_turn": 12,
        "subject_prefix": "HARVEST — G6",
        "created_at": "2026-09-22T02:06:39Z",
        "body_fields": ["RULING:", "NEXT_ADMIT:"],
    }
    out = None
    for k in range(0, 200):
        consult = dict(base_consult)
        consult["subject"] = f"CONSULT_PENDING {em} G6" + (em * k)
        sections = _minimal_sections(
            fetch_lanes={"lanes": lanes},
            fetch_consults={
                "open_consults": [consult],
                "open_consults_outside_house": [],
                "truncated_meta": {},
            },
        )
        candidate = assemble_packet(sections=sections, options={"root": "12286"})
        nb = packet_nbytes(candidate)
        if 12224 <= nb <= 12288 and candidate["meta"]["truncated"].get("lanes"):
            out = candidate
            break
    assert out is not None, "no near-cap packet found"
    assert out["meta"]["generated_at"] == "1970-01-01T00:00:00Z"
    assert out["meta"]["latency_ms"] == 99999999
    meta_keys = set(out["meta"])

    init = Path(__file__).resolve().parent / "__init__.py"
    spec = importlib.util.spec_from_file_location(
        "_pipeline_handlers_maestro_induct_v1",
        init,
        submodule_search_locations=[str(init.parent)],
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    # A prior load of this synthetic package caches submodules. Re-executing
    # __init__.py does not rebind _clients onto the new module object, and
    # importing it from __init__ would put _clients on the AC4 runtime walk.
    cpath = init.parent / "_clients.py"
    cspec = importlib.util.spec_from_file_location(
        f"{spec.name}._clients",
        cpath,
    )
    clients = importlib.util.module_from_spec(cspec)
    sys.modules[cspec.name] = clients
    cspec.loader.exec_module(clients)
    t0 = [1700000000.0]

    def now():
        t0[0] += 0.01
        return t0[0]

    monkeypatch.setattr(clients, "now_epoch", now)
    from .emit import MaestroInductEmitHandler, _clients as emit_clients  # noqa: I001

    if emit_clients is not clients:
        monkeypatch.setattr(emit_clients, "now_epoch", now)

    handler = MaestroInductEmitHandler()
    ctx = types.SimpleNamespace(
        outputs={
            "assemble": types.SimpleNamespace(json=out),
            "resolve": types.SimpleNamespace(json={"started_epoch": t0[0] - 1.0}),
        }
    )
    step = types.SimpleNamespace()
    result = asyncio.run(handler.execute(step, ctx))
    raw = result.raw
    assert len(raw.encode("utf-8")) <= 12288
    parsed = json_mod.loads(raw)
    assert set(parsed["meta"]) == meta_keys


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
