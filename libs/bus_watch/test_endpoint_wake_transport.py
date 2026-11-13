"""Unit tests for StoreClient.subscription routing in endpoint_wake_transport."""

from __future__ import annotations

from typing import Any

from bus_watch.endpoint_wake_transport import StoreClient


def _lane_stub_store(
    subs: list[dict[str, Any]],
    lane_map: dict[str, str | None],
) -> StoreClient:
    lane_gets: list[str] = []

    class StubStore(StoreClient):
        def _bus(self, method: str, path: str, **kwargs: object) -> object:
            if path == "/endpoint-subscriptions":
                return {"subscriptions": subs}
            if path.startswith("/threads/") and path.endswith("/lane-current"):
                tid = path.split("/")[2]
                lane_gets.append(path)
                parent = lane_map.get(tid)
                if parent is None:
                    return {"parent_thread": None}
                return {"parent_thread": parent}
            raise AssertionError(f"unexpected _bus {method} {path}")

    store = StubStore()
    store._lane_gets = lane_gets  # type: ignore[attr-defined]
    return store


def test_grok_thread_scoped_parent_mailbox_does_not_match_child_wake() -> None:
    parent = "23609"
    child = "23622"
    subs = [
        {
            "id": 7044,
            "state": "subscribed",
            "thread_scope": parent,
            "scope_kind": "thread",
            "surface": "grok",
            "display": "10",
        },
    ]
    store = _lane_stub_store(subs, {child: parent, parent: None})
    assert store.subscription("s5-land-batcher", child) is None
    assert store._lane_gets == []  # type: ignore[attr-defined]


def test_grok_exact_mailbox_still_matches() -> None:
    thread = "23609"
    subs = [
        {
            "id": 7044,
            "state": "subscribed",
            "thread_scope": thread,
            "scope_kind": "thread",
            "surface": "grok",
        },
    ]
    store = _lane_stub_store(subs, {thread: None})
    result = store.subscription("s5-land-batcher", thread)
    assert result is not None
    assert result[0] == "exact"
    assert result[1]["id"] == 7044


def test_grok_wildcard_still_matches_child() -> None:
    child = "23622"
    subs = [
        {
            "id": 90,
            "state": "subscribed",
            "thread_scope": "*",
            "scope_kind": "thread",
            "surface": "grok",
        },
    ]
    store = _lane_stub_store(subs, {child: "23609", "23609": None})
    result = store.subscription("s5-land-batcher", child)
    assert result is not None
    assert result[0] == "wildcard"


def test_grok_scope_kind_tree_opt_in_parent_route() -> None:
    parent = "23609"
    child = "23622"
    subs = [
        {
            "id": 80,
            "state": "subscribed",
            "thread_scope": parent,
            "scope_kind": "tree",
            "surface": "grok",
        },
    ]
    store = _lane_stub_store(subs, {child: parent, parent: None})
    result = store.subscription("s5-land-batcher", child)
    assert result is not None
    assert result[0] == "parent"
    assert len(store._lane_gets) == 2  # type: ignore[attr-defined]


def test_grok_parent_wake_evidence_opt_in_parent_route() -> None:
    parent = "23609"
    child = "23622"
    subs = [
        {
            "id": 81,
            "state": "subscribed",
            "thread_scope": parent,
            "scope_kind": "thread",
            "surface": "grok",
            "evidence": "parent_wake=1",
        },
    ]
    store = _lane_stub_store(subs, {child: parent, parent: None})
    result = store.subscription("s5-land-batcher", child)
    assert result is not None
    assert result[0] == "parent"


def test_cursor_parent_route_unchanged_for_closeout_chain() -> None:
    lane_map = {"19521": "19510", "19510": "19263", "19263": None}
    subs = [{"id": 1, "state": "subscribed", "thread_scope": "19263"}]
    store = _lane_stub_store(subs, lane_map)
    result = store.subscription("cursor", "19521")
    assert result is not None
    assert result[0] == "parent"
    assert len(store._lane_gets) == 3  # type: ignore[attr-defined]
