"""friction:42127 — single prepare pass when admit plan already carries markdown."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from implement_admission.admission_read import compute_packet_sha256
from spawn_ledger.spawn_endpoint_house_ledger import (
    _fold_spawn_rows,
    _read_rows,
    ledger_path,
    mint_row_id,
    project_row_status,
)

from . import conductor_admit_enrich as enrich_mod
from . import generate_wrap as gw
from .admission import FrontierEndpointError
from .conductor_admit_enrich import enrich_conductor_admit_plan
from .generate_wrap import (
    GenerateWrapResult,
    dispatch_cursor_sdk_generate_route,
)
from .route import TeamDispatchGenerateBody


@pytest.fixture
def watch_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("spawn_ledger.spawn_endpoint_house_ledger.WATCH_DIR", tmp_path)
    return tmp_path


def _folded(watch_dir: Path) -> dict[str, dict[str, Any]]:
    return _fold_spawn_rows(_read_rows(ledger_path(watch_dir)))


def _conductor_body() -> TeamDispatchGenerateBody:
    return TeamDispatchGenerateBody(
        op="generate",
        seat="cursor-sdk",
        dispatch_thread_id="22253",
        job="conductor",
        lane="B",
        source_ref="todo:friction-42127",
        caller_agent="cursor-sdk",
        work_key="friction:42127",
    )


def _base_plan(markdown: str = "# commissioned packet\n") -> dict[str, Any]:
    return {
        "packet_markdown": markdown,
        "packet_sha256": "deadbeef",
        "packet_rel_path": "packets/x.md",
    }


def _patch_route_collaborators(
    monkeypatch: pytest.MonkeyPatch, sdk_mock: AsyncMock
) -> None:
    monkeypatch.setattr(gw, "dispatch_cursor_sdk_generate", sdk_mock)
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 0}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("22253", None, False)),
    )
    from .route_conductor_commit import ConductorCommitResult

    monkeypatch.setattr(
        "systems.frontier_consult.route_conductor_commit.commit_conductor_plan",
        lambda *_a, **_k: ConductorCommitResult(
            packet_path="/p", packet_sha256="sha", birth_seed_ignored=False
        ),
    )


@pytest.mark.asyncio
async def test_admit_plan_skips_second_prepare_and_prefixes_message(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(a)(b) enriched plan: prepare must not run; message carries ledger prefix + sha."""
    fixed_row = mint_row_id()
    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.mint_row_id", lambda: fixed_row
    )
    sdk_mock = AsyncMock(return_value={"dispatch_id": "d1", "thread_id": "22253"})
    _patch_route_collaborators(monkeypatch, sdk_mock)

    def _must_not_run(**_kw: Any) -> GenerateWrapResult:
        raise AssertionError("second prepare pass")

    monkeypatch.setattr(gw, "prepare_conductor_packet", _must_not_run)
    plan = _base_plan()
    base_md = plan["packet_markdown"]

    await dispatch_cursor_sdk_generate_route(
        request_id="r-a",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
        admit_plan=plan,
    )

    call = sdk_mock.await_args
    message_text = call.kwargs["message_text"]
    expected_prefix = f"ledger_row: {fixed_row}\npurpose: (unstated)\n"
    assert message_text == expected_prefix + base_md
    assert plan["packet_markdown"] == base_md
    assert compute_packet_sha256(message_text) == compute_packet_sha256(
        expected_prefix + base_md
    )


@pytest.mark.asyncio
async def test_conductor_route_gated_fails_closed(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gated bridge: 422, spawn row failed, dispatch not called, one prepare."""
    counter = {"n": 0}

    def _prepare(**_kw: Any) -> GenerateWrapResult:
        counter["n"] += 1
        return GenerateWrapResult(
            packet_path=None,
            gated=True,
            gated_reason="judgment_required",
        )

    monkeypatch.setattr(enrich_mod, "prepare_conductor_packet", _prepare)
    monkeypatch.setattr(gw, "prepare_conductor_packet", _prepare)
    sdk_mock = AsyncMock(return_value={"dispatch_id": "d-gated"})
    _patch_route_collaborators(monkeypatch, sdk_mock)

    result = await dispatch_cursor_sdk_generate_route(
        request_id="r-gated-route",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
        admit_plan={},
    )

    assert getattr(result, "status_code", None) == 422
    assert b"generate_source_ref_gated" in result.body
    sdk_mock.assert_not_awaited()
    assert counter["n"] == 1
    rows = list(_folded(watch_dir).values())
    assert len(rows) == 1
    assert rows[0]["state"] == "failed"
    assert rows[0]["stage"] == "materialize"
    assert rows[0]["reason"] == "generate_source_ref_gated"


@pytest.mark.asyncio
async def test_without_plan_markdown_prepare_runs_once_with_row_id(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(c) no plan markdown — exactly one prepare, row_id wired."""
    sdk_mock = AsyncMock(return_value={"dispatch_id": "d1"})
    _patch_route_collaborators(monkeypatch, sdk_mock)
    calls: list[dict[str, Any]] = []

    def _prepare(**kw: Any) -> GenerateWrapResult:
        calls.append(dict(kw))
        return GenerateWrapResult(
            packet_path=None,
            packet_markdown="# from prepare",
            materialized=True,
        )

    monkeypatch.setattr(gw, "prepare_conductor_packet", _prepare)

    await dispatch_cursor_sdk_generate_route(
        request_id="r-c",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
        admit_plan={},
    )

    assert len(calls) == 1
    assert calls[0]["row_id"] is not None
    assert str(calls[0]["row_id"]).strip()


@pytest.mark.asyncio
async def test_enrich_then_route_single_prepare(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(d) enrich + route end-to-end — prepare counter reads 1 across both modules."""
    counter = {"n": 0}

    def _prepare(**_kw: Any) -> GenerateWrapResult:
        counter["n"] += 1
        return GenerateWrapResult(
            packet_path=None,
            packet_markdown="# once",
            materialized=True,
            packet_sha256="abc",
            birth_rows=("R1", "R2"),
        )

    monkeypatch.setattr(enrich_mod, "prepare_conductor_packet", _prepare)
    monkeypatch.setattr(gw, "prepare_conductor_packet", _prepare)
    sdk_mock = AsyncMock(return_value={"dispatch_id": "d-enrich"})
    _patch_route_collaborators(monkeypatch, sdk_mock)
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value=None))

    body = _conductor_body()
    enriched = await enrich_conductor_admit_plan(body=body, plan={}, request_id="r-d")
    await dispatch_cursor_sdk_generate_route(
        request_id="r-d",
        body=body,
        seat="cursor-sdk",
        response=Response(),
        admit_plan=enriched,
    )
    assert counter["n"] == 1


@pytest.mark.asyncio
async def test_birth_rows_from_enrich_reach_commit(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(e) birth_rows on enriched plan flow into commit_conductor_plan."""
    captured: list[Any] = []

    def _prepare(**_kw: Any) -> GenerateWrapResult:
        return GenerateWrapResult(
            packet_path=None,
            packet_markdown="# pkt",
            materialized=True,
            packet_sha256="s",
            birth_rows=("R1", "R2"),
        )

    monkeypatch.setattr(enrich_mod, "prepare_conductor_packet", _prepare)
    monkeypatch.setattr(gw, "prepare_conductor_packet", _prepare)
    sdk_mock = AsyncMock(return_value={"dispatch_id": "d-birth"})
    _patch_route_collaborators(monkeypatch, sdk_mock)
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value=None))

    def _capture(plan: Any, dispatch_id: str, **kw: Any) -> Any:
        captured.append(plan)
        from .route_conductor_commit import ConductorCommitResult

        return ConductorCommitResult(
            packet_path="/x",
            packet_sha256="s",
            birth_seed_ignored=False,
        )

    monkeypatch.setattr(
        "systems.frontier_consult.route_conductor_commit.commit_conductor_plan",
        _capture,
    )

    body = _conductor_body()
    enriched = await enrich_conductor_admit_plan(body=body, plan={}, request_id="r-e")
    await dispatch_cursor_sdk_generate_route(
        request_id="r-e",
        body=body,
        seat="cursor-sdk",
        response=Response(),
        admit_plan=enriched,
    )
    assert captured
    assert tuple(captured[0].birth_rows) == ("R1", "R2")


@pytest.mark.asyncio
async def test_dispatch_frontier_error_fails_row_with_code(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(f) dispatch 503 — row failed with exception code as reason."""
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 0}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("22253", None, False)),
    )
    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# p", materialized=True
        ),
    )
    monkeypatch.setattr(
        gw,
        "dispatch_cursor_sdk_generate",
        AsyncMock(
            side_effect=FrontierEndpointError(
                request_id="r-f",
                field="dispatch",
                reason="busy",
                status_code=503,
                code="restart_busy_deferred",
            )
        ),
    )

    result = await dispatch_cursor_sdk_generate_route(
        request_id="r-f",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
    )
    assert getattr(result, "status_code", None) == 503
    rows = list(_folded(watch_dir).values())
    assert len(rows) == 1
    assert rows[0]["state"] == "failed"
    assert rows[0]["stage"] == "dispatch"
    assert rows[0]["reason"] == "restart_busy_deferred"
    assert (
        project_row_status(rows[0]["row_id"], watch_dir=watch_dir)["status"] == "failed"
    )


@pytest.mark.asyncio
async def test_dispatch_cancelled_fails_row_and_propagates(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(g) CancelledError propagates; row marked dispatch/CancelledError."""
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 0}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("22253", None, False)),
    )
    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# p", materialized=True
        ),
    )
    monkeypatch.setattr(
        gw,
        "dispatch_cursor_sdk_generate",
        AsyncMock(side_effect=asyncio.CancelledError()),
    )

    with pytest.raises(asyncio.CancelledError):
        await dispatch_cursor_sdk_generate_route(
            request_id="r-g",
            body=_conductor_body(),
            seat="cursor-sdk",
            response=Response(),
        )

    row = list(_folded(watch_dir).values())[0]
    assert row["state"] == "failed"
    assert row["stage"] == "dispatch"
    assert row["reason"] == "CancelledError"


@pytest.mark.asyncio
async def test_dispatch_empty_dict_fails_row_dispatch_no_id(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(h) dispatch returns {} — row failed at dispatch_no_id."""
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 0}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("22253", None, False)),
    )
    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# p", materialized=True
        ),
    )
    monkeypatch.setattr(gw, "dispatch_cursor_sdk_generate", AsyncMock(return_value={}))

    result = await dispatch_cursor_sdk_generate_route(
        request_id="r-h",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
    )
    assert isinstance(result, dict)
    assert not str(result.get("dispatch_id") or "").strip()
    row = list(_folded(watch_dir).values())[0]
    assert row["state"] == "failed"
    assert row["stage"] == "dispatch_no_id"


@pytest.mark.asyncio
async def test_dispatch_id_success_leaves_live_row(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(i) dispatch_id present — live row, no failure patch."""
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 0}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("22253", None, False)),
    )
    monkeypatch.setattr(
        gw,
        "prepare_conductor_packet",
        lambda **_kw: GenerateWrapResult(
            packet_path=None, packet_markdown="# p", materialized=True
        ),
    )
    sdk_mock = AsyncMock(return_value={"dispatch_id": "d1"})
    _patch_route_collaborators(monkeypatch, sdk_mock)

    await dispatch_cursor_sdk_generate_route(
        request_id="r-i",
        body=_conductor_body(),
        seat="cursor-sdk",
        response=Response(),
    )

    row = list(_folded(watch_dir).values())[0]
    assert row["state"] == "live"
    assert row.get("dispatch_id") == "d1"
    assert row.get("reason") is None


@pytest.mark.asyncio
async def test_parallel_admits_do_not_mutate_shared_plan(
    watch_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """(j) two admits share one plan dict — distinct prefixes, plan markdown unchanged."""
    row_ids = iter([mint_row_id(), mint_row_id()])

    monkeypatch.setattr(
        "spawn_ledger.spawn_endpoint_house_ledger.mint_row_id",
        lambda: next(row_ids),
    )
    monkeypatch.setattr(gw, "probe_thread", AsyncMock(return_value={"turn_count": 0}))
    monkeypatch.setattr(
        gw,
        "refuse_conductor_operator_lane_summon",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        gw,
        "resolve_cursor_sdk_thread_targets",
        AsyncMock(return_value=("22253", None, False)),
    )

    def _must_not_run(**_kw: Any) -> GenerateWrapResult:
        raise AssertionError("prepare during shared-plan admits")

    monkeypatch.setattr(gw, "prepare_conductor_packet", _must_not_run)
    sdk = AsyncMock(return_value={"dispatch_id": "d-par"})
    _patch_route_collaborators(monkeypatch, sdk)

    plan = _base_plan("shared body\n")
    original_md = plan["packet_markdown"]
    body = _conductor_body()

    await asyncio.gather(
        dispatch_cursor_sdk_generate_route(
            request_id="r-j1",
            body=body,
            seat="cursor-sdk",
            response=Response(),
            admit_plan=plan,
        ),
        dispatch_cursor_sdk_generate_route(
            request_id="r-j2",
            body=body,
            seat="cursor-sdk",
            response=Response(),
            admit_plan=plan,
        ),
    )

    assert plan["packet_markdown"] == original_md
    messages = [c.kwargs["message_text"] for c in sdk.await_args_list]
    assert len(messages) == 2
    assert messages[0] != messages[1]
    for msg in messages:
        assert msg.endswith(original_md)
        assert msg.startswith("ledger_row:")
