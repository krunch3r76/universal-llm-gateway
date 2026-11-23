"""Hub E2: wake routing to parked houseless tab seats (Packet 2 AC-5, AC-6)."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.cse_associations import (
    HOUSELESS_FIRST_SEATED_BOUND_BY,
    associate_cse,
)
from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint
from agent_bus_store.db.endpoint_wake_ledger import CONSUMER, advance_scan, get_cursor
from agent_bus_store.testing.wake_cursor_test_support import rewind_wake_cursor_for_test

from bus_watch.cse_wake_houseless_guard import ASSOCIATION_MISMATCH
from bus_watch.endpoint_wake_dispatcher import (
    _HOUSELESS_NO_ROUTE,
    resolve_cse_wake_target,
    scan_once,
)

pytestmark = pytest.mark.offline

_REG = "583b411df34247f9a48bc044b6518be3"
_REG2 = "succ583b411df34247f9a48bc044b6518be4"
_CHAT = "https://claude.ai/chat/2762063d-2966-4859-a3ce-23831c6e0c86"
_CHAT2 = "https://claude.ai/chat/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_AGENT = "ulg-cortex-symbiosis"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _tab_payload(rid: str, chat: str, **overrides: object) -> dict[str, object]:
    row = {
        "registration_id": rid,
        "purpose": "houseless",
        "status": "tab",
        "seat_open": False,
        "chat_url": chat,
    }
    row.update(overrides)
    return row


class _ProvenanceClient:
    def __init__(
        self,
        *,
        mapping: dict[str, dict[str, object]],
        timeout_s: float = 5.0,
    ) -> None:
        self._mapping = mapping
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str) -> dict[str, object]:
        del method
        qs = parse_qs(urlparse(path).query)
        rid = (qs.get("registration_id") or [""])[0]
        if rid in self._mapping:
            return dict(self._mapping[rid])
        return {"registration_id": rid}


def _patch_provenance(
    monkeypatch: pytest.MonkeyPatch, mapping: dict[str, dict[str, object]]
) -> None:
    from cdp_ask.houseless_resolve import clear_houseless_resolve_memo

    clear_houseless_resolve_memo()

    class _Factory:
        def __init__(self, timeout_s: float = 5.0) -> None:
            self.timeout_s = timeout_s
            self._inner = _ProvenanceClient(mapping=mapping, timeout_s=timeout_s)

        def _request(self, method: str, path: str) -> dict[str, object]:
            return self._inner._request(method, path)

    monkeypatch.setattr(
        "cdp_ask.operator_seat_resolve.CdpAskClient",
        lambda timeout_s=5.0: _Factory(timeout_s=timeout_s),
    )


def _scan_store_base() -> dict[str, Any]:
    return {"pending": [], "scanned": []}


def _make_scan_store(
    *,
    holder_by_reg: dict[str, dict[str, Any]] | None = None,
    holder_by_thread: dict[str, dict[str, Any]] | None = None,
    parent_for: dict[str, str] | None = None,
) -> type:
    holder_by_reg = holder_by_reg or {}
    holder_by_thread = holder_by_thread or {}
    parent_for = parent_for or {}
    state = _scan_store_base()

    class Store:
        def cursor(self) -> dict[str, int]:
            return get_cursor()

        def since(self, after_id: int, limit: int = 50) -> list[dict[str, Any]]:
            with connect() as c:
                rows = c.execute(
                    "SELECT id, thread, turn_number, from_agent, to_agent, subject, "
                    "body, read_at FROM turns WHERE id>? ORDER BY id LIMIT ?",
                    (after_id, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        def scan(self, expected: int, new_hw: int, rows: list[dict]) -> dict:
            state["scanned"].extend(rows)
            for row in rows:
                if row.get("state") != "skipped":
                    state["pending"].append(row)
            return advance_scan(
                "endpoint-wake-dispatcher",
                expected,
                new_hw,
                rows,
                now="2020-01-01 20:00:00",
            )

        def subscription(self, agent: str, thread_id: str, raw_to: str | None = None):
            from agent_bus_store.db.endpoint_subscriptions import current_subscription

            if thread_id in parent_for:
                row = current_subscription(agent, parent_for[thread_id])
                if row and row.get("state") == "subscribed":
                    return ("parent", row)
                return None
            row = current_subscription(agent, thread_id)
            if row and row.get("state") == "subscribed":
                return ("exact", row)
            return None

        def holder(
            self, lane_thread_id: str, **kwargs: object
        ) -> dict[str, Any] | None:
            return holder_by_thread.get(lane_thread_id)

        def holder_by_registration(self, registration_id: str) -> dict[str, Any] | None:
            return holder_by_reg.get(registration_id)

        def lane_parent(self, thread_id: str) -> str | None:
            return parent_for.get(thread_id)

    Store._state = state  # type: ignore[attr-defined]
    return Store


def _setup_thread_and_wake(*, agent_id: str) -> tuple[str, dict[str, Any], int]:
    detail = create_thread(thread_id=None, slug="houseless-tab-wake")
    assert detail is not None
    thread = str(detail["id"])
    sub = subscribe_endpoint(
        agent=_AGENT,
        thread_scope=thread,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=agent_id,
        bound_by="test",
    )
    turn_id, _ts, _num, _c = insert_turn(
        thread=thread,
        from_agent="houseless-driver",
        to_agent=_AGENT,
        subject="QUESTION|wake",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    return thread, sub, turn_id


def _ledger_row(turn_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT state, reason, subscription_id FROM endpoint_wake_ledger WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
    return dict(row) if row else None


def _sub_dict(sub: dict[str, Any], thread: str) -> dict[str, Any]:
    row = sub.get("row") or sub
    return {
        "surface": "cse",
        "scope_kind": "thread",
        "agent_id": str(row.get("agent_id") or ""),
        "thread_scope": thread,
        "id": row.get("id"),
        "state": row.get("state"),
    }


def test_scan_routes_parked_tab_seat(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    from cdp_ask.houseless_resolve import clear_houseless_resolve_memo

    _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
    thread, sub, turn_id = _setup_thread_and_wake(agent_id=_REG)
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT,
        cse_registration_id=_REG,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    store_cls = _make_scan_store()
    scan_once(store_cls())  # type: ignore[arg-type]
    state = store_cls._state  # type: ignore[attr-defined]
    assert not [
        r for r in state["scanned"] if r.get("reason") == "no_route:scan_holder"
    ]
    ledger = _ledger_row(turn_id)
    assert ledger is None or ledger["reason"] != "no_route:scan_holder"
    clear_houseless_resolve_memo()
    target = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=True,
    )
    assert target is not None
    _lane, holder = target
    assert holder["registration_id"] == _REG
    assert holder["chat_url"] == _CHAT
    assert holder["seat_state"] == "houseless"
    assert holder.get("houseless_tab") is True


def test_successor_tab_seat_routed(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provenance(
        monkeypatch,
        {
            _REG: _tab_payload(_REG, _CHAT),
            _REG2: _tab_payload(_REG2, _CHAT2),
        },
    )
    thread, sub, _turn_id = _setup_thread_and_wake(agent_id=_REG)
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT,
        cse_registration_id=_REG,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT2,
        cse_registration_id=_REG2,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    store_cls = _make_scan_store()
    scan_once(store_cls())  # type: ignore[arg-type]
    ledger = _ledger_row(_turn_id)
    assert ledger is None or ledger.get("reason") != "no_route:scan_holder"
    target = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=True,
    )
    assert target is not None
    _lane, holder = target
    assert holder["registration_id"] == _REG2
    assert holder["chat_url"] == _CHAT2


def test_successor_without_fields_refuses_fallback(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_provenance(
        monkeypatch,
        {_REG: _tab_payload(_REG, _CHAT), _REG2: {"registration_id": _REG2}},
    )
    thread, sub, turn_id = _setup_thread_and_wake(agent_id=_REG)
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT2,
        cse_registration_id=_REG2,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    store_cls = _make_scan_store()
    target = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=False,
    )
    assert target is None
    target_del = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=True,
    )
    assert target_del is not None
    assert target_del[1].get(_HOUSELESS_NO_ROUTE) == ASSOCIATION_MISMATCH


def test_rebound_chat_refused(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
    thread, sub, turn_id = _setup_thread_and_wake(agent_id=_REG)
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT2,
        cse_registration_id=_REG,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    store_cls = _make_scan_store()
    scan_once(store_cls())  # type: ignore[arg-type]
    ledger = _ledger_row(turn_id)
    assert ledger is not None
    assert ledger["reason"] == "no_route:scan_holder"
    target = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=True,
    )
    assert target is not None
    assert target[1].get(_HOUSELESS_NO_ROUTE) == ASSOCIATION_MISMATCH


def test_no_association_refused(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
    thread, sub, turn_id = _setup_thread_and_wake(agent_id=_REG)
    store_cls = _make_scan_store()
    scan_once(store_cls())  # type: ignore[arg-type]
    ledger = _ledger_row(turn_id)
    assert ledger is not None
    assert ledger["reason"] == "no_route:scan_holder"


def test_released_row_unchanged(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provenance(monkeypatch, {_REG: {"registration_id": _REG}})
    thread, sub, turn_id = _setup_thread_and_wake(agent_id=_REG)
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT,
        cse_registration_id=_REG,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    store_cls = _make_scan_store()
    scan_once(store_cls())  # type: ignore[arg-type]
    ledger = _ledger_row(turn_id)
    assert ledger is not None
    assert ledger["reason"] == "no_route:scan_holder"


def test_no_bus_database_refuses(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
    thread, sub, _turn_id = _setup_thread_and_wake(agent_id=_REG)
    associate_cse(
        thread_id=thread,
        cse_chat_url=_CHAT,
        cse_registration_id=_REG,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    store_cls = _make_scan_store()
    monkeypatch.delenv("AGENT_BUS_DB_PATH", raising=False)
    scan = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=False,
    )
    assert scan is None
    delivery = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=True,
    )
    assert delivery is not None
    assert delivery[1].get(_HOUSELESS_NO_ROUTE) == ASSOCIATION_MISMATCH


def test_giw_candidate_refused(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
    thread, sub, turn_id = _setup_thread_and_wake(agent_id="pred-agent")
    store_cls = _make_scan_store(
        holder_by_thread={
            thread: {
                "registration_id": _REG,
                "chat_url": _CHAT,
                "seat_state": "driving",
            }
        }
    )
    scan_once(store_cls())  # type: ignore[arg-type]
    ledger = _ledger_row(turn_id)
    assert ledger is not None
    assert ledger["reason"] == "no_route:scan_holder"


def test_parent_route_child_thread_refused(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
    house = create_thread(thread_id=None, slug="houseless-tab-house")
    worker = create_thread(thread_id=None, slug="houseless-tab-worker")
    assert house is not None and worker is not None
    house_id = str(house["id"])
    worker_id = str(worker["id"])
    subscribe_endpoint(
        agent=_AGENT,
        thread_scope=house_id,
        surface="cse",
        display="cse",
        title="cowork",
        agent_id=_REG,
        bound_by="test",
    )
    associate_cse(
        thread_id=house_id,
        cse_chat_url=_CHAT,
        cse_registration_id=_REG,
        bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
        holder_projection=False,
    )
    turn_id, _ts, _num, _c = insert_turn(
        thread=worker_id,
        from_agent="houseless-driver",
        to_agent=_AGENT,
        subject="QUESTION|parent route",
        body="body",
    )
    get_cursor()
    with connect() as conn:
        rewind_wake_cursor_for_test(conn, CONSUMER, to=0)
    store_cls = _make_scan_store(parent_for={worker_id: house_id})
    scan_once(store_cls())  # type: ignore[arg-type]
    state = store_cls._state  # type: ignore[attr-defined]
    assert not state["pending"]
    ledger = _ledger_row(turn_id)
    assert ledger is not None
    assert ledger["reason"] == "no_route:scan_holder"
    sub = {
        "surface": "cse",
        "scope_kind": "thread",
        "agent_id": _REG,
        "thread_scope": worker_id,
        "id": 1,
    }
    target = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        sub,
        wake_thread=worker_id,
        delivery_phase=True,
    )
    assert target is not None
    assert target[1].get(_HOUSELESS_NO_ROUTE) == ASSOCIATION_MISMATCH


@pytest.mark.parametrize(
    "case",
    ["i-b", "ii", "iii", "vi"],
)
def test_delivery_skips_with_association_mismatch(
    db: None, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:

    mapping: dict[str, dict[str, object]] = {}
    if case in {"i-b", "ii", "iii"}:
        mapping[_REG] = _tab_payload(_REG, _CHAT)
    if case == "i-b":
        mapping[_REG2] = {"registration_id": _REG2}
    thread, sub, turn_id = _setup_thread_and_wake(agent_id=_REG)
    if case == "i-b":
        associate_cse(
            thread_id=thread,
            cse_chat_url=_CHAT2,
            cse_registration_id=_REG2,
            bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
            holder_projection=False,
        )
    elif case == "ii":
        associate_cse(
            thread_id=thread,
            cse_chat_url=_CHAT2,
            cse_registration_id=_REG,
            bound_by=HOUSELESS_FIRST_SEATED_BOUND_BY,
            holder_projection=False,
        )
    elif case == "vi":
        _patch_provenance(monkeypatch, {_REG: _tab_payload(_REG, _CHAT)})
        store_cls = _make_scan_store(
            holder_by_thread={
                thread: {
                    "registration_id": _REG,
                    "chat_url": _CHAT,
                    "seat_state": "driving",
                }
            }
        )
        target = resolve_cse_wake_target(
            store_cls(),  # type: ignore[arg-type]
            _sub_dict(sub, thread),
            wake_thread=thread,
            delivery_phase=True,
        )
        assert target is not None
        assert target[1].get(_HOUSELESS_NO_ROUTE) == ASSOCIATION_MISMATCH
        return
    _patch_provenance(monkeypatch, mapping)
    store_cls = _make_scan_store()
    target = resolve_cse_wake_target(
        store_cls(),  # type: ignore[arg-type]
        _sub_dict(sub, thread),
        wake_thread=thread,
        delivery_phase=True,
    )
    assert target is not None
    assert target[1].get(_HOUSELESS_NO_ROUTE) == ASSOCIATION_MISMATCH
