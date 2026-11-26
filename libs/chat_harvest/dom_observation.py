"""DOM observation collector orchestration — readiness, collect, top-level response."""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any

from chat_harvest.dom_observation_poll import poll_until_stable, poll_until_turns
from chat_harvest.dom_observation_request import (
    MESSAGE_SELECTORS,
    WALL_BUDGET_S,
    open_tab_state,
    script_config,
)
from chat_harvest.dom_observation_shape import (
    attach_page,
    collect_observation,
    failure_observation_response,
    observation_response,
    stamp_tab_close_and_methods,
)
from chat_harvest.readonly_cdp import CollectorScriptError, ReadonlyCDP


def _observe_after_ready(
    *,
    cdp: ReadonlyCDP,
    conn: Any,
    cfg: dict[str, Any],
    cfg_ready: dict[str, Any],
    chat_url: str,
    conversation_id: str,
    match_terms: list[str],
    max_nodes: int,
    scope: str,
    readiness: dict[str, Any],
    tab_info: dict[str, Any],
    deadline: float,
    clock: Callable[[], float],
    budget_left: Callable[[], float],
) -> dict[str, Any]:
    poll_until_stable(
        cdp,
        conn,
        cfg_ready,
        readiness=readiness,
        deadline=deadline,
        clock=clock,
        budget_left=budget_left,
    )
    if budget_left() <= 0:
        return failure_observation_response(
            outcome="unreachable",
            code="timeout",
            looked=False,
            chat_url=chat_url,
            conversation_id=conversation_id,
            match_terms=match_terms,
            max_nodes=max_nodes,
            scope=scope,
            tab=tab_info,
            cdp_methods={},
            readiness=readiness,
        )
    observation = collect_observation(
        cdp,
        conn,
        cfg,
        readiness=readiness,
        tab_info=tab_info,
        match_terms=match_terms,
        max_nodes=max_nodes,
        scope=scope,
    )
    return observation_response(
        outcome="observed",
        looked=True,
        chat_url=chat_url,
        conversation_id=conversation_id,
        observation=observation,
        opened_on_demand=False,
    )


def _run_on_connected_page(
    *,
    cdp: ReadonlyCDP,
    conn: Any,
    chat_url: str,
    conversation_id: str,
    match_terms: list[str],
    max_nodes: int,
    scope: str,
    readiness: dict[str, Any],
    tab_info: dict[str, Any],
    deadline: float,
    clock: Callable[[], float],
    budget_left: Callable[[], float],
) -> dict[str, Any]:
    cfg = script_config(match_terms, max_nodes)
    cfg_ready = {"message_sel": MESSAGE_SELECTORS}
    ready_signal = poll_until_turns(
        cdp,
        conn,
        cfg_ready,
        readiness=readiness,
        tab_info=tab_info,
        deadline=deadline,
        clock=clock,
        budget_left=budget_left,
    )
    if ready_signal and ready_signal.get("login_wall"):
        return failure_observation_response(
            outcome="unauthenticated",
            code=None,
            looked=False,
            chat_url=chat_url,
            conversation_id=conversation_id,
            match_terms=match_terms,
            max_nodes=max_nodes,
            scope=scope,
            tab=tab_info,
            cdp_methods={},
            readiness=readiness,
        )
    return _observe_after_ready(
        cdp=cdp,
        conn=conn,
        cfg=cfg,
        cfg_ready=cfg_ready,
        chat_url=chat_url,
        conversation_id=conversation_id,
        match_terms=match_terms,
        max_nodes=max_nodes,
        scope=scope,
        readiness=readiness,
        tab_info=tab_info,
        deadline=deadline,
        clock=clock,
        budget_left=budget_left,
    )


def _execute_dom_observation(
    *,
    cdp: ReadonlyCDP,
    chat_url: str,
    conversation_id: str,
    match_terms: list[str],
    max_nodes: int,
    open_on_demand: bool,
    scope: str,
    readiness: dict[str, Any],
    tab_info: dict[str, Any],
    deadline: float,
    clock: Callable[[], float],
    budget_left: Callable[[], float],
) -> tuple[Any | None, dict[str, Any]]:
    conn, _minted, early = attach_page(
        cdp, chat_url, open_on_demand=open_on_demand, tab_info=tab_info
    )
    if early is not None:
        early["conversation_id"] = conversation_id
        early["url"] = chat_url
        return conn, early
    body = _run_on_connected_page(
        cdp=cdp,
        conn=conn,
        chat_url=chat_url,
        conversation_id=conversation_id,
        match_terms=match_terms,
        max_nodes=max_nodes,
        scope=scope,
        readiness=readiness,
        tab_info=tab_info,
        deadline=deadline,
        clock=clock,
        budget_left=budget_left,
    )
    return conn, body


def run_dom_observation(
    *,
    chat_url: str,
    conversation_id: str,
    cdp_url: str,
    match_terms: list[str],
    max_nodes: int,
    open_on_demand: bool,
    scope: str,
    transport: ReadonlyCDP | None = None,
    monotonic: Callable[[], float] | None = None,
) -> dict[str, Any]:
    clock = monotonic or time.monotonic
    deadline = clock() + WALL_BUDGET_S
    cdp = transport or ReadonlyCDP(cdp_url)
    readiness = {"polls": 0, "waited_ms": 0, "turns_seen": False, "stable": False}
    tab_info = open_tab_state()
    conn: Any = None
    result: dict[str, Any] | None = None

    def budget_left() -> float:
        return deadline - clock()

    try:
        conn, result = _execute_dom_observation(
            cdp=cdp,
            chat_url=chat_url,
            conversation_id=conversation_id,
            match_terms=match_terms,
            max_nodes=max_nodes,
            open_on_demand=open_on_demand,
            scope=scope,
            readiness=readiness,
            tab_info=tab_info,
            deadline=deadline,
            clock=clock,
            budget_left=budget_left,
        )
    except CollectorScriptError:
        result = failure_observation_response(
            outcome="unreachable",
            code="collector_error",
            looked=False,
            chat_url=chat_url,
            conversation_id=conversation_id,
            match_terms=match_terms,
            max_nodes=max_nodes,
            scope=scope,
            tab=tab_info,
            cdp_methods={},
            readiness=readiness,
        )
    except Exception:
        result = failure_observation_response(
            outcome="unreachable",
            code="unreachable",
            looked=False,
            chat_url=chat_url,
            conversation_id=conversation_id,
            match_terms=match_terms,
            max_nodes=max_nodes,
            scope=scope,
            tab=tab_info,
            cdp_methods={},
            readiness=readiness,
        )
    finally:
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()
        stamp_tab_close_and_methods(cdp=cdp, tab_info=tab_info, result=result)
    return result
