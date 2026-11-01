"""hop_rebind — predecessor subscription repoint after continuity resume."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import init_db
from agent_bus_store.db.endpoint_subscriptions import (
    list_subscriptions,
    subscribe_endpoint,
)
from agent_bus_store.server import create_app
from fastapi.testclient import TestClient

from bus_watch.hop_rebind import (
    rebind_predecessor_subscriptions,
    scope_reaches_root,
)

pytestmark = pytest.mark.offline

_PRIOR = "11111111-1111-1111-1111-111111111111"
_NEW = "22222222-2222-2222-2222-222222222222"
_FOREIGN = "33333333-3333-3333-3333-333333333333"


@pytest.fixture()
def bus_client(tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    init_db()
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _thread(client: TestClient, slug: str) -> str:
    resp = client.post("/threads", json={"slug": slug})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["id"])


def _cursor_sub(scope: str, agent_id: str, *, title: str = "t") -> None:
    subscribe_endpoint(
        agent="cursor",
        thread_scope=scope,
        surface="cursor",
        display="8",
        title=title,
        agent_id=agent_id,
    )


def _lane_parent(client: TestClient) -> Any:
    def _fn(thread_id: str) -> str | None:
        payload = client.get(f"/threads/{thread_id}/lane-current").json()
        parent = payload.get("parent_thread")
        return str(parent).strip() if parent else None

    return _fn


def _db_subscribe(scope: str, new_id: str, **kwargs: Any) -> dict[str, Any]:
    title = kwargs.get("title")
    display = kwargs.get("display") or "8"
    bound_by = kwargs.get("bound_by")
    evidence = kwargs.get("evidence")
    subscribe_endpoint(
        agent="cursor",
        thread_scope=scope,
        surface="cursor",
        display=str(display),
        title=title,
        agent_id=new_id,
        bound_by=bound_by,
        evidence=evidence,
    )
    return {"ok": True}


def test_scope_reaches_root_chain() -> None:
    parents = {"child": "root", "grand": "child"}

    def lane_parent(tid: str) -> str | None:
        return parents.get(tid)

    assert scope_reaches_root("root", "root", lane_parent) is True
    assert scope_reaches_root("*", "root", lane_parent) is True
    assert scope_reaches_root("child", "root", lane_parent) is True
    assert scope_reaches_root("grand", "root", lane_parent) is True
    assert scope_reaches_root("other-root", "root", lane_parent) is False


def test_rebind_repoints_root_child_and_wildcard_mock() -> None:
    root = "10479"
    child = "21571"
    rows = [
        {
            "agent": "cursor",
            "thread_scope": root,
            "state": "subscribed",
            "agent_id": _PRIOR,
            "display": "8",
            "title": "root title",
        },
        {
            "agent": "cursor",
            "thread_scope": "*",
            "state": "subscribed",
            "agent_id": _PRIOR,
            "display": "8",
            "title": "wild",
        },
        {
            "agent": "cursor",
            "thread_scope": child,
            "state": "subscribed",
            "agent_id": _PRIOR,
            "display": "8",
            "title": "lane",
        },
        {
            "agent": "cursor",
            "thread_scope": "99999",
            "state": "subscribed",
            "agent_id": _PRIOR,
            "display": "8",
            "title": "foreign mailbox",
        },
        {
            "agent": "cursor",
            "thread_scope": root,
            "state": "subscribed",
            "agent_id": _FOREIGN,
            "display": "8",
            "title": "other tab",
        },
    ]
    subscribed: list[tuple[str, str]] = []

    def fake_sub(scope: str, aid: str, **kwargs: Any) -> dict[str, Any]:
        subscribed.append((scope, aid))
        return {"ok": True}

    def lane_parent(tid: str) -> str | None:
        return root if tid == child else None

    out = rebind_predecessor_subscriptions(
        root,
        _NEW,
        prior_agent_id=_PRIOR,
        list_subscriptions=lambda: rows,
        lane_parent=lane_parent,
        subscribe=fake_sub,
    )
    assert out["ok"] is True
    assert {r["thread_scope"] for r in out["repointed"]} == {root, "*", child}
    assert any(s["reason"] == "other_mailbox" for s in out["skipped"])
    assert any(s["reason"] == "foreign_agent_id" for s in out["skipped"])
    assert subscribed == [(root, _NEW), ("*", _NEW), (child, _NEW)]


def test_rebind_idempotent_second_call() -> None:
    root = "10479"
    rows = [
        {
            "agent": "cursor",
            "thread_scope": root,
            "state": "subscribed",
            "agent_id": _NEW,
            "display": "8",
            "title": "t",
        }
    ]

    def fake_sub(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("should not subscribe")

    out = rebind_predecessor_subscriptions(
        root,
        _NEW,
        prior_agent_id=_PRIOR,
        list_subscriptions=lambda: rows,
        lane_parent=lambda _t: None,
        subscribe=fake_sub,
    )
    assert out["ok"] is True
    assert out["repointed"] == []
    assert len(out["skipped"]) == 1


def test_rebind_integration_db(bus_client: TestClient) -> None:
    root = _thread(bus_client, "hop-rebind-root")
    child = _thread(bus_client, "hop-rebind-child")
    foreign_root = _thread(bus_client, "hop-rebind-foreign")
    bus_client.post(
        f"/threads/{child}/lane-bind",
        json={"parent_thread_id": root, "lane_role": "sub_mission"},
    )
    _cursor_sub(root, _PRIOR, title="root")
    _cursor_sub(child, _PRIOR, title="child")
    _cursor_sub(foreign_root, _PRIOR, title="foreign")

    out = rebind_predecessor_subscriptions(
        root,
        _NEW,
        prior_agent_id=_PRIOR,
        list_subscriptions=lambda: list_subscriptions(agent="cursor"),
        lane_parent=_lane_parent(bus_client),
        subscribe=_db_subscribe,
    )
    assert out["ok"] is True
    scopes = {r["thread_scope"] for r in out["repointed"]}
    assert scopes == {root, child}
    assert any(s["reason"] == "other_mailbox" for s in out["skipped"])

    cur_root = list_subscriptions(agent="cursor", thread_scope=root)[0]
    cur_child = list_subscriptions(agent="cursor", thread_scope=child)[0]
    cur_foreign = list_subscriptions(agent="cursor", thread_scope=foreign_root)[0]
    assert cur_root["agent_id"] == _NEW
    assert cur_child["agent_id"] == _NEW
    assert cur_foreign["agent_id"] == _PRIOR

    again = rebind_predecessor_subscriptions(
        root,
        _NEW,
        prior_agent_id=_PRIOR,
        list_subscriptions=lambda: list_subscriptions(agent="cursor"),
        lane_parent=_lane_parent(bus_client),
        subscribe=_db_subscribe,
    )
    assert again["ok"] is True
    assert again["repointed"] == []


def test_resume_path_rebind_and_retire() -> None:
    from tools.continuity_ops import maybe_subscribe_cursor_xvfb_on_resume

    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch("tools.continuity_ops.claim_fable_lock", return_value={"ok": True}),
        patch(
            "tools.continuity_ops.current_root_subscription_agent_id",
            return_value=_PRIOR,
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True, "agent_id": _NEW},
        ),
        patch(
            "tools.continuity_ops.rebind_predecessor_subscriptions",
            return_value={"ok": True, "repointed": [{"thread_scope": "10479"}]},
        ) as mock_rebind,
        patch(
            "tools.continuity_ops.retire_departing_tab",
            return_value={"ok": True, "phase": "retired"},
        ) as mock_retire,
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="10479",
            transcript_id=_NEW,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    mock_rebind.assert_called_once()
    assert mock_rebind.call_args.args == ("10479", _NEW)
    assert mock_rebind.call_args.kwargs["prior_agent_id"] == _PRIOR
    mock_retire.assert_called_once_with(
        "10479",
        f"ide:{_PRIOR}",
        transcript_id=_PRIOR,
    )
    assert out["child_lane_rebind"]["ok"] is True
    assert out["predecessor_retire"]["phase"] == "retired"


def test_resume_skips_retire_when_prior_matches_new() -> None:
    from tools.continuity_ops import maybe_subscribe_cursor_xvfb_on_resume

    with (
        patch("tools.continuity_ops.detect_cursor_gui_host", return_value=":8"),
        patch("tools.continuity_ops.is_cursor_xvfb_host", return_value=True),
        patch("tools.continuity_ops.claim_fable_lock", return_value={"ok": True}),
        patch(
            "tools.continuity_ops.current_root_subscription_agent_id",
            return_value=_NEW,
        ),
        patch(
            "tools.continuity_ops.subscribe_cursor_xvfb_house",
            return_value={"ok": True},
        ),
        patch(
            "tools.continuity_ops.rebind_predecessor_subscriptions",
            return_value={"ok": True, "repointed": []},
        ),
        patch("tools.continuity_ops.retire_departing_tab") as mock_retire,
        patch(
            "tools.continuity_ops.cleanup_xvfb_sidebar_on_holder_claim",
            return_value={"ok": True},
        ),
    ):
        out = maybe_subscribe_cursor_xvfb_on_resume(
            thread="10479",
            transcript_id=_NEW,
            surface="cursor",
            resume_result={"fence_id": "f1"},
        )
    mock_retire.assert_not_called()
    assert "predecessor_retire" not in out
