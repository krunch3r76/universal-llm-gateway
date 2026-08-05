"""G5 F-collision gate/meter provocations (F-6a/b, F-7).

Non-collected. Imported by harness_g5_f_collision.py. Arc agent-bus:6792.
"""

from __future__ import annotations

import asyncio
import os
import tempfile


async def _force_release(*ids: str) -> None:
    from services.git_integration_worker.cursor_sdk_gate import (
        force_release_sdk_dispatch_slot,
    )

    for did in ids:
        try:
            await force_release_sdk_dispatch_slot(dispatch_id=did)
        except Exception:
            pass


def provoke_f6a() -> int:
    """F-6 widening: nest at limit=3 must not leak a 4th top-level slot."""
    from services.git_integration_worker.cursor_sdk_gate import (
        acquire_sdk_dispatch_slot,
        sdk_dispatch_gate_stats,
        transfer_sdk_dispatch_slot,
    )

    async def _run() -> int:
        os.environ["CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY"] = "3"
        os.environ["CURSOR_SDK_DISPATCH_CONCURRENCY"] = "1"
        await _force_release(
            "auto-op-1",
            "auto-op-2",
            "auto-op-3",
            "auto-op-4",
            "auto-op-1-child",
        )
        await acquire_sdk_dispatch_slot(dispatch_id="auto-op-1", timeout=2)
        await acquire_sdk_dispatch_slot(dispatch_id="auto-op-2", timeout=2)
        await acquire_sdk_dispatch_slot(dispatch_id="auto-op-3", timeout=2)
        await transfer_sdk_dispatch_slot(
            from_id="auto-op-1", to_id="auto-op-1-child"
        )
        nest_stats = sdk_dispatch_gate_stats(lane="operator")
        leaked = False
        try:
            await acquire_sdk_dispatch_slot(dispatch_id="auto-op-4", timeout=0.25)
            leaked = True
        except TimeoutError:
            pass
        active = int(sdk_dispatch_gate_stats(lane="operator")["active"])
        print(
            "F-6a nest_stats=",
            nest_stats,
            "leaked=",
            leaked,
            "active=",
            active,
        )
        await _force_release(
            "auto-op-1-child", "auto-op-2", "auto-op-3", "auto-op-4"
        )
        if leaked or active > 3 or int(nest_stats["active"]) != 3:
            print("F-6a_REPRODUCED")
            return 2
        print("F-6a_SURVIVED")
        return 0

    return asyncio.run(_run())


def provoke_f6b() -> int:
    """F-6 deadlock: nest+transfer at limit=1 must keep active==1."""
    from services.git_integration_worker.cursor_sdk_gate import (
        acquire_sdk_dispatch_slot,
        sdk_dispatch_gate_stats,
        transfer_sdk_dispatch_slot,
    )
    from services.git_integration_worker.cursor_auto.gate_serialize import (
        plan_nested_dispatch,
    )

    async def _run() -> int:
        os.environ["CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY"] = "1"
        await _force_release(
            "auto-parent", "auto-child", "auto-parent2", "auto-naive-child"
        )
        await acquire_sdk_dispatch_slot(dispatch_id="auto-parent", timeout=2)
        await transfer_sdk_dispatch_slot(
            from_id="auto-parent", to_id="auto-child"
        )
        nest_stats = sdk_dispatch_gate_stats(lane="operator")
        await _force_release("auto-child")
        await acquire_sdk_dispatch_slot(dispatch_id="auto-parent2", timeout=2)
        plan = plan_nested_dispatch(work_bounded=True, park_available=True)
        naive_blocked = False
        try:
            await acquire_sdk_dispatch_slot(
                dispatch_id="auto-naive-child", timeout=0.25
            )
        except TimeoutError:
            naive_blocked = True
        print(
            "F-6b nest_stats=",
            nest_stats,
            "plan=",
            plan,
            "naive_blocked=",
            naive_blocked,
        )
        await _force_release("auto-parent2", "auto-naive-child", "auto-child")
        if int(nest_stats["active"]) != 1:
            print("F-6b_REPRODUCED")
            return 2
        print("F-6b_SURVIVED")
        return 0

    return asyncio.run(_run())


def provoke_f7() -> int:
    """F-7: published capacity scalars must match multi-A lease occupancy."""
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )
    from services.git_integration_worker.cursor_sdk_gate import (
        sdk_dispatch_gate_stats,
    )
    from services.git_integration_worker.cursor_sdk_lane_regime import (
        set_lane_b_regime,
    )
    from services.git_integration_worker.models.cursor_api import (
        CursorDispatchRequest,
        CursorDispatchResponse,
    )
    from scripts.model_manager.ui.dispatch_monitor.core.sdk_posture import (
        live_writer_count,
        posture_legend,
        classify_sdk_live,
    )
    from scripts.model_manager.ui.dispatch_monitor.core.dtos import (
        SdkDispatchRow,
    )

    data = tempfile.mkdtemp(prefix="g5-f7-")
    os.environ["DATA_DIR"] = data
    os.environ["CURSOR_SDK_OPERATOR_MULTI_A_ENABLED"] = "1"
    os.environ["CURSOR_SDK_OPERATOR_DISPATCH_CONCURRENCY"] = "3"
    os.environ["CURSOR_SDK_DISPATCH_CONCURRENCY"] = "1"
    CursorDispatchLedger._instance = None
    set_lane_b_regime(active=False)
    ledger = CursorDispatchLedger.instance()
    repo = "/repo-f7-harness"
    for did in ("auto-meter-0", "auto-meter-1", "auto-meter-2"):
        req = CursorDispatchRequest(
            thread_id=did,
            model="cursor/composer-2.5",
            dispatch_id=did,
            execution_id=f"exec-{did}",
            message="hi",
            lane="A",
        )
        ledger.admit(
            req=req,
            fingerprint=ledger.fingerprint(req),
            execution_id=req.execution_id,
            caller_agent="cursor-auto",
            resolved_model="composer-2.5",
            admission=CursorDispatchResponse(
                admitted=True,
                dispatch_id=did,
                thread_id=did,
                model_id="composer-2.5",
            ),
            contract="implement",
            source_repo=repo,
            lease_key=repo,
            concurrency_posture="multi_a_operator",
            write_lease_slot_limit=3,
        )
    snap = ledger.lease_snapshot(source_repo=repo)
    observed = len(snap["active_holders"])
    stats = sdk_dispatch_gate_stats()
    live = int(stats.get("live_writers") or 0)
    abl = stats.get("active_by_lane") or {}
    board = live_writer_count(active_by_lane=abl)  # type: ignore[arg-type]
    wc = int(stats.get("write_capacity") or 0)
    rows = [
        SdkDispatchRow(
            dispatch_id=f"auto-meter-{i}",
            state="running",
            root_id="R",
            last_tool_name="edit" if i == 0 else None,
        )
        for i in range(2)
    ]
    posture = classify_sdk_live(rows)
    legend = posture_legend(posture) or ""
    div: list[str] = []
    if live != observed:
        div.append(f"live_writers={live}!=holders={observed}")
    if board != observed:
        div.append(f"board={board}!=holders={observed}")
    if wc < observed and observed > 1:
        div.append(f"write_capacity={wc}<holders={observed}")
    if posture == "id_split" and "writer count" not in legend.lower():
        div.append("id_split legend lacks writer-count disclaimer")
    print(
        "F-7 holders=",
        observed,
        "live_writers=",
        live,
        "board=",
        board,
        "write_capacity=",
        wc,
        "detail=",
        stats.get("write_capacity_detail"),
        "posture=",
        posture,
        "div=",
        div,
    )
    CursorDispatchLedger._instance = None
    os.environ.pop("CURSOR_SDK_OPERATOR_MULTI_A_ENABLED", None)
    if div:
        print("F-7_REPRODUCED")
        return 2
    print("F-7_SURVIVED")
    return 0
