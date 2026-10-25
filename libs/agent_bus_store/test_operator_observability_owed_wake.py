"""Owed_wake hooks on send, bulk mark-read, and manage notice exclusion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_bus_store import create_app
from agent_bus_store.auth import require_token
from agent_bus_store.db.connection import write_connect
from agent_bus_store.db.turns import insert_turn
from agent_bus_store.operator_observability_hooks import after_turn_created
from scripts.model_manager.ui.controller.owed_wake_ledger import (
    flush_pending_owed_wakes,
    is_chrome_bus_subject,
)
from scripts.model_manager.ui.controller.restart_intent_store import (
    RestartIntentStore,
)
from scripts.model_manager.ui.controller.tests.test_owed_wake_ledger import (
    _deps,
)


def _bus_app(tmp_path):
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None
    return app


def _sidecar_app(tmp_path, monkeypatch: pytest.MonkeyPatch):
    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    import cortex_store.dispatch_ops._thread_sidecar as sidecar_mod

    monkeypatch.setattr(sidecar_mod, "_FILES_ROOT", cortex_root)
    return _bus_app(tmp_path), cortex_root


def _bind_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> RestartIntentStore:
    store = RestartIntentStore(db_path=tmp_path / "restart-intents.db")
    monkeypatch.setattr(
        RestartIntentStore,
        "instance",
        classmethod(lambda _cls: store),
    )
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        lambda _lane: "holder-maestro",
    )
    return store


def test_build_owed_wake_deps_imports_without_stub() -> None:
    from services.git_integration_worker.cse_session_holders import (
        build_owed_wake_deps,
    )

    deps = build_owed_wake_deps()
    assert deps.post_bus_turn is not None
    assert deps.endpoint_wake is not None


def test_send_route_milestone_to_maestro_records_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-send-milestone",
                "from": "cursor",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
        )
        assert create.status_code == 201, create.text
        thread_id = create.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "maestro",
                "subject": "MILESTONE|decision:operator-observability-ledger|via-send",
                "body": "house milestone via send path",
            },
        )
        assert resp.status_code == 201, resp.text
    open_rows = store.list_open_undispatched_owed_wakes()
    assert len(open_rows) == 1
    assert open_rows[0].holder_id == "holder-maestro"


def test_on_house_milestone_records_row_when_deps_import_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cse_session_holders import (
        on_house_milestone_to_maestro,
    )

    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.build_owed_wake_deps",
        MagicMock(side_effect=ImportError("broken")),
    )
    out = on_house_milestone_to_maestro(
        subject="MILESTONE|decision:operator-observability-ledger|deps-fail",
        to_agent="maestro",
        from_agent="cursor",
        thread_id="18460",
        turn_number=3,
        lane_thread_id="18460",
    )
    assert out["recorded"] is True
    assert len(store.list_open_undispatched_owed_wakes()) == 1


def test_sidecar_continue_send_milestone_records_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    app, _ = _sidecar_app(tmp_path, monkeypatch)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-sidecar-continue",
                "from": "cursor",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
        )
        thread_id = create.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "maestro",
                "subject": "MILESTONE|decision:operator-observability-ledger|sidecar",
                "body": "pointer only",
                "sidecar_content": "## Milestone body\n",
            },
        )
        assert resp.status_code == 201, resp.text
    assert len(store.list_open_undispatched_owed_wakes()) == 1


def test_second_drain_coalesce_does_not_add_open_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.model_manager.ui.controller.owed_wake_ledger import (
        producer2_on_restart_terminal,
    )
    from scripts.model_manager.ui.controller.restart_intent_store import (
        STATUS_COMPLETED,
        STATUS_DRAINED_RESTARTING,
    )
    from scripts.model_manager.ui.controller.tests.test_owed_wake_ledger import (
        _deps,
    )

    store = _bind_store(tmp_path, monkeypatch)
    deps = _deps(post_ok=True, endpoint_ok=True)
    for suffix in ("a", "b"):
        intent = store.create_intent(
            service="git_integration_worker",
            action="restart",
            deadline_at="d",
            reason=f"r-{suffix}",
            caller_thread_id="18460",
        )
        store.advance_if_status(
            intent.intent_id,
            from_status="pending_drain",
            to_status=STATUS_DRAINED_RESTARTING,
            reason="drained",
        )
        store.advance_if_status(
            intent.intent_id,
            from_status=STATUS_DRAINED_RESTARTING,
            to_status=STATUS_COMPLETED,
            reason="done",
        )
        completed = store.get(intent.intent_id)
        assert completed is not None
        producer2_on_restart_terminal(
            store, completed, holder_id="holder-maestro", deps=deps
        )
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM owed_wake "
            "WHERE holder_id=? AND closed_at IS NULL",
            ("holder-maestro",),
        ).fetchone()["c"]
    assert n == 1


def test_holder_bulk_mark_read_closes_open_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-bulk-read",
                "from": "cursor",
                "to": "maestro",
                "subject": "seed",
                "body": "body",
            },
        )
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        store.coalesce_owed_wake_milestone(
            holder_id="holder-maestro",
            lane_thread_id=thread_id,
            turn_ref={
                "thread_id": thread_id,
                "turn_number": 1,
                "subject": "MILESTONE|x",
            },
        )
        resp = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"turn_numbers": [1], "agent": "maestro"},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["marked_read"] >= 1
    assert store.close_owed_wake_for_holder("holder-maestro") == 0


def test_non_holder_bulk_mark_read_leaves_owed_wake_row_open(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-non-holder-read",
                "from": "cursor",
                "to": "maestro",
                "subject": "seed",
                "body": "body",
            },
        )
        thread_id = create.json()["thread"]["id"]
        store.coalesce_owed_wake_milestone(
            holder_id="holder-maestro",
            lane_thread_id=thread_id,
            turn_ref={
                "thread_id": thread_id,
                "turn_number": 1,
                "subject": "MILESTONE|x",
            },
        )
        resp = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"turn_numbers": [1], "agent": "cursor"},
        )
        assert resp.status_code == 200, resp.text
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM owed_wake "
            "WHERE holder_id=? AND closed_at IS NULL",
            ("holder-maestro",),
        ).fetchone()["c"]
    assert n == 1


def test_manage_notice_does_not_close_open_owed_wake_row(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    store.coalesce_owed_wake_milestone(
        holder_id="holder-maestro",
        lane_thread_id="18460",
        turn_ref={"thread_id": "18460", "turn_number": 1, "subject": "MILESTONE|x"},
    )
    after_turn_created(
        thread_id="18460",
        to_agent="cursor",
        from_agent="manage",
        subject="FACT|restart_intent terminal — owed wake (intent-1)",
        turn_number=2,
    )
    with store._connect() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM owed_wake "
            "WHERE holder_id=? AND closed_at IS NULL",
            ("holder-maestro",),
        ).fetchone()["c"]
    assert n == 1


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _bus_app_with_token(
    tmp_path, monkeypatch: pytest.MonkeyPatch, token: str = "owed-wake-bus-token"
):
    db_path = str(tmp_path / "bus.db")
    monkeypatch.setenv("AGENT_BUS_TOKEN", token)
    monkeypatch.setenv("AGENT_BUS_DB_PATH", db_path)
    import agent_bus_store.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_TOKEN", token)
    return create_app(db_path=db_path), token


def _tracking_build_deps(monkeypatch: pytest.MonkeyPatch, endpoint_calls: list) -> None:
    def factory():
        deps = _deps()
        deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
        return deps

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.build_owed_wake_deps",
        factory,
    )


def _open_owed_wake_count(
    store: RestartIntentStore, holder_id: str = "holder-maestro"
) -> int:
    with store._connect() as conn:
        return int(
            conn.execute(
                "SELECT COUNT(*) AS c FROM owed_wake "
                "WHERE holder_id=? AND closed_at IS NULL",
                (holder_id,),
            ).fetchone()["c"]
        )


def test_fact_cdp_reply_to_web_anthropic_records_row_and_dispatches_once(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = "MILESTONE|cdp reply latency fixed"
    assert is_chrome_bus_subject(subject) is False
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    _tracking_build_deps(monkeypatch, endpoint_calls)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-fact-cdp-reply",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web-anthropic",
                "subject": subject,
                "body": "house fact with cdp reply substring in slug",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
    assert _open_owed_wake_count(store) == 1
    assert len(endpoint_calls) == 1


def test_fact_to_web_anthropic_reroutes_and_does_not_record_owed_wake(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    subject = "FACT|cdp reply latency fixed"
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    _tracking_build_deps(monkeypatch, endpoint_calls)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-fact-reroute",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        assert create.status_code == 201, create.text
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        resp = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web-anthropic",
                "subject": subject,
                "body": "house fact rerouted at compose",
            },
            headers=headers,
        )
        assert resp.status_code == 201, resp.text
        turn = client.get(
            f"/turns/by-number?thread={thread_id}&turn_number=2",
            headers=headers,
        ).json()
        assert turn["to"] == "cursor"
    assert _open_owed_wake_count(store) == 0
    assert endpoint_calls == []
    after_turn_created(
        thread_id=thread_id,
        to_agent="maestro",
        from_agent="cursor",
        subject="FACT|forced-via-turns",
        turn_number=3,
    )
    assert _open_owed_wake_count(store) == 0
    assert endpoint_calls == []


@pytest.mark.parametrize("holder_seat", ["maestro", "web-anthropic"])
def test_milestone_mark_read_then_second_milestone_redispatches(
    tmp_path, monkeypatch: pytest.MonkeyPatch, holder_seat: str
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    _tracking_build_deps(monkeypatch, endpoint_calls)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": f"owed-read-seq-{holder_seat}",
                "from": "cursor",
                "to": holder_seat,
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        first = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": holder_seat,
                "subject": "MILESTONE|decision:operator-observability-ledger|one",
                "body": "first",
            },
            headers=headers,
        )
        assert first.status_code == 201, first.text
        assert _open_owed_wake_count(store) == 1
        assert len(endpoint_calls) == 1
        read = client.patch(
            f"/threads/{thread_id}/turns/read-state",
            json={"turn_numbers": [2], "agent": holder_seat},
            headers=headers,
        )
        assert read.status_code == 200, read.text
        assert _open_owed_wake_count(store) == 0
        second = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": holder_seat,
                "subject": "MILESTONE|decision:operator-observability-ledger|two",
                "body": "second",
            },
            headers=headers,
        )
        assert second.status_code == 201, second.text
    assert _open_owed_wake_count(store) == 1
    assert len(endpoint_calls) == 2


@pytest.mark.parametrize("holder_seat", ["maestro", "web-anthropic"])
def test_milestone_holder_post_then_second_milestone_redispatches(
    tmp_path, monkeypatch: pytest.MonkeyPatch, holder_seat: str
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    _tracking_build_deps(monkeypatch, endpoint_calls)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": f"owed-post-seq-{holder_seat}",
                "from": "cursor",
                "to": holder_seat,
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        first = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": holder_seat,
                "subject": "MILESTONE|decision:operator-observability-ledger|one",
                "body": "first",
            },
            headers=headers,
        )
        assert first.status_code == 201, first.text
        assert len(endpoint_calls) == 1
        holder_post = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": holder_seat,
                "to": "cursor",
                "subject": "ACK|read milestone",
                "body": "holder activity closes owed_wake",
            },
            headers=headers,
        )
        assert holder_post.status_code == 201, holder_post.text
        assert _open_owed_wake_count(store) == 0
        second = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": holder_seat,
                "subject": "MILESTONE|decision:operator-observability-ledger|two",
                "body": "second",
            },
            headers=headers,
        )
        assert second.status_code == 201, second.text
    assert _open_owed_wake_count(store) == 1
    assert len(endpoint_calls) == 2


def _wire_fetch_to_test_client(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    import transport_utils
    import transport_utils.client_factory as client_factory

    class _SyncBusClient:
        def __init__(self, tc: TestClient) -> None:
            self._tc = tc

        def get(self, path: str, **kwargs):
            return self._tc.get(path, **kwargs)

        def post(self, path: str, **kwargs):
            return self._tc.post(path, **kwargs)

        def __enter__(self) -> _SyncBusClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def _make(
        url: str, *, timeout: float = 10.0, follow_redirects: bool = False
    ) -> _SyncBusClient:
        del url, timeout, follow_redirects
        return _SyncBusClient(client)

    monkeypatch.setattr(client_factory, "make_sync_client", _make)
    monkeypatch.setattr(transport_utils, "make_sync_client", _make)


def _insert_stale_unread_milestone(
    *,
    thread_id: str,
    to_agent: str,
    age_minutes: float,
) -> int:
    old_ts = (datetime.now(UTC) - timedelta(minutes=age_minutes)).isoformat()
    _turn_id, _ts, turn_number, _claimed = insert_turn(
        thread=thread_id,
        from_agent="cursor",
        to_agent=to_agent,
        subject="MILESTONE|decision:operator-observability-ledger|stale-backstop",
        body="stale unread milestone for lazy projection",
    )
    with write_connect() as conn:
        conn.execute(
            "UPDATE turns SET created_at = ? WHERE thread = ? AND turn_number = ?",
            (old_ts, thread_id, turn_number),
        )
    return turn_number


def test_flush_projects_stale_milestone_via_authenticated_turns_route(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        _wire_fetch_to_test_client(monkeypatch, client)
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-flush-stale",
                "from": "cursor",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        stale_turn = _insert_stale_unread_milestone(
            thread_id=thread_id,
            to_agent="maestro",
            age_minutes=16,
        )
        assert stale_turn == 2
        monkeypatch.setattr(
            "scripts.model_manager.ui.controller.owed_wake_ledger._lanes_for_stale_projection",
            lambda: [thread_id],
        )
        assert flush_pending_owed_wakes(store, deps=deps) == 1
    assert len(endpoint_calls) == 1
    assert store.list_open_undispatched_owed_wakes() == []


def test_flush_stale_projection_missing_token_projects_zero(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        _wire_fetch_to_test_client(monkeypatch, client)
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-flush-no-token",
                "from": "cursor",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        thread_id = create.json()["thread"]["id"]
        _insert_stale_unread_milestone(
            thread_id=thread_id,
            to_agent="maestro",
            age_minutes=16,
        )
        monkeypatch.setattr(
            "scripts.model_manager.ui.controller.owed_wake_ledger._lanes_for_stale_projection",
            lambda: [thread_id],
        )
        monkeypatch.delenv("AGENT_BUS_TOKEN", raising=False)
        assert flush_pending_owed_wakes(store, deps=deps) == 0
    assert endpoint_calls == []
    assert store.list_open_undispatched_owed_wakes() == []


@pytest.mark.parametrize("closed", [False, True])
def test_flush_stale_projection_skips_already_dispatched_milestone(
    tmp_path, monkeypatch: pytest.MonkeyPatch, closed: bool
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    deps = _deps()
    deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        _wire_fetch_to_test_client(monkeypatch, client)
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": f"owed-flush-delivered-{'closed' if closed else 'open'}",
                "from": "cursor",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        thread_id = create.json()["thread"]["id"]
        stale_turn = _insert_stale_unread_milestone(
            thread_id=thread_id,
            to_agent="maestro",
            age_minutes=16,
        )
        row, _ = store.coalesce_owed_wake_milestone(
            holder_id="holder-maestro",
            lane_thread_id=thread_id,
            turn_ref={
                "thread_id": thread_id,
                "turn_number": stale_turn,
                "subject": "MILESTONE|decision:operator-observability-ledger|stale-backstop",
            },
        )
        store.mark_owed_wake_dispatched(row.row_id, outcome="dispatched")
        if closed:
            store.close_owed_wake_for_holder("holder-maestro")
        monkeypatch.setattr(
            "scripts.model_manager.ui.controller.owed_wake_ledger._lanes_for_stale_projection",
            lambda: [thread_id],
        )
        assert flush_pending_owed_wakes(store, deps=deps) == 0
    assert endpoint_calls == []
    fetched = store.get_owed_wake(row.row_id)
    assert fetched is not None
    assert len(fetched.turn_refs) == 1


def test_get_turns_mark_read_closes_owed_wake_for_web_anthropic(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")
    app, token = _bus_app_with_token(tmp_path, monkeypatch)
    headers = _auth_headers(token)
    with TestClient(app) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-get-turns-mark-read",
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "bootstrap",
                "body": "seed",
            },
            headers=headers,
        )
        thread_id = create.json()["thread"]["id"]
        monkeypatch.setattr(
            "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
            lambda lane: "holder-maestro" if lane == thread_id else None,
        )
        milestone = client.post(
            "/threads/send",
            json={
                "thread": thread_id,
                "from": "cursor",
                "to": "web-anthropic",
                "subject": "MILESTONE|decision:operator-observability-ledger|get-turns",
                "body": "milestone",
            },
            headers=headers,
        )
        assert milestone.status_code == 201, milestone.text
        assert _open_owed_wake_count(store) == 1
        read = client.get(
            "/turns",
            params={
                "thread": thread_id,
                "to": "web-anthropic",
                "mark_read": True,
                "mark_read_seat": "web-anthropic",
                "last": 10,
            },
            headers=headers,
        )
        assert read.status_code == 200, read.text
    assert _open_owed_wake_count(store) == 0


def test_schedule_force_gate_notice_wrapper_no_recursion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.model_manager.ui.controller import restart_drain_force_gate as gate

    called: list[dict] = []
    monkeypatch.setattr(
        gate,
        "_schedule_force_gate_notice_impl",
        lambda **kw: called.append(kw),
    )
    monkeypatch.setattr(
        "scripts.model_manager.ui.controller.owed_wake_ledger.maybe_flush_pending_owed_wakes",
        MagicMock(return_value=0),
    )
    gate._schedule_force_gate_notice(thread_id="1", subject="s", body="b")
    assert len(called) == 1


_MAESTRO_MILESTONE_HEADS = (
    "QUESTION",
    "RULING",
    "DONE",
    "BLOCKED",
    "FAILED",
    "LANDED",
    "VERDICT",
)


@pytest.mark.parametrize("head", _MAESTRO_MILESTONE_HEADS)
def test_grok_coordinator_milestone_on_child_thread_resolves_lane_12286_holder(
    tmp_path, monkeypatch: pytest.MonkeyPatch, head: str
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")

    def _resolve(lane: str) -> str | None:
        return "holder-12286" if lane == "12286" else None

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        _resolve,
    )
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": f"owed-grok-child-lane-{head.lower()}",
                "from": "grok-coordinator-1",
                "to": "maestro",
                "subject": "bootstrap",
                "body": "seed",
            },
        )
        assert create.status_code == 201, create.text
        child_lane = create.json()["thread"]["id"]
        resp = client.post(
            "/threads/send",
            json={
                "thread": child_lane,
                "from": "grok-coordinator-1",
                "to": "maestro",
                "subject": f"{head}|friction:40219",
                "body": "milestone to maestro on child thread",
            },
        )
        assert resp.status_code == 201, resp.text
    open_rows = store.list_open_undispatched_owed_wakes()
    assert len(open_rows) == 1
    assert open_rows[0].holder_id == "holder-12286"
    assert open_rows[0].lane_thread_id == "12286"


def test_grok_coordinator_milestone_dispatches_after_lane_12286_resolve(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.git_integration_worker.cse_session_holders import (
        dispatch_owed_wake_producer1_row,
        on_house_milestone_to_maestro,
    )

    _bind_store(tmp_path, monkeypatch)
    endpoint_calls: list[dict] = []
    live_holder = {
        "holder_id": "holder-12286",
        "seat_state": "driving",
        "chat_url": "https://claude.ai/c/x",
        "registration_id": "reg-12286",
        "lane_thread_id": "12286",
    }

    def factory():
        deps = _deps(holder=live_holder)
        deps.holder_lookup = lambda hid: live_holder if hid == "holder-12286" else None  # type: ignore[method-assign]
        deps.endpoint_wake = lambda **kw: endpoint_calls.append(kw) or True  # type: ignore[method-assign]
        return deps

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.build_owed_wake_deps",
        factory,
    )
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "1")

    def _resolve(lane: str) -> str | None:
        return "holder-12286" if lane == "12286" else None

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        _resolve,
    )
    out = on_house_milestone_to_maestro(
        subject="QUESTION|friction:40219-dispatch",
        to_agent="maestro",
        from_agent="grok-coordinator-1",
        thread_id="15851",
        turn_number=2,
        lane_thread_id="15851",
    )
    assert out.get("recorded") is True
    assert out.get("needs_deferred_dispatch") is True
    dispatch_owed_wake_producer1_row(str(out["row_id"]))
    assert len(endpoint_calls) == 1
    assert endpoint_calls[0]["holder_id"] == "holder-12286"


@pytest.mark.parametrize("subject", ["ACK|re #1", "ROUTE|handoff"])
def test_ack_and_route_to_maestro_do_not_record_owed_wake(
    tmp_path, monkeypatch: pytest.MonkeyPatch, subject: str
) -> None:
    store = _bind_store(tmp_path, monkeypatch)
    monkeypatch.setenv("OPERATOR_OBSERVABILITY_PRODUCER1_CSE_DISPATCH", "off")

    def _resolve(lane: str) -> str | None:
        return "holder-12286" if lane == "12286" else None

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        _resolve,
    )
    out = after_turn_created(
        thread_id="15851",
        to_agent="maestro",
        from_agent="grok-coordinator-1",
        subject=subject,
        turn_number=2,
    )
    assert out is not None
    assert out.get("recorded") is False
    assert store.list_open_undispatched_owed_wakes() == []


def test_holder_mark_read_on_child_lane_closes_12286_owed_wake(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _bind_store(tmp_path, monkeypatch)

    def _resolve(lane: str) -> str | None:
        return "holder-12286" if lane == "12286" else None

    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.resolve_maestro_holder_id_for_lane",
        _resolve,
    )
    with TestClient(_bus_app(tmp_path)) as client:
        create = client.post(
            "/threads/with-turn",
            json={
                "slug": "owed-close-fallback",
                "from": "cursor",
                "to": "maestro",
                "subject": "seed",
                "body": "body",
            },
        )
        assert create.status_code == 201, create.text
        child_lane = create.json()["thread"]["id"]
        store.coalesce_owed_wake_milestone(
            holder_id="holder-12286",
            lane_thread_id="12286",
            turn_ref={
                "thread_id": child_lane,
                "turn_number": 2,
                "subject": "QUESTION|x",
            },
        )
        resp = client.patch(
            f"/threads/{child_lane}/turns/read-state",
            json={"turn_numbers": [1], "agent": "maestro"},
        )
        assert resp.status_code == 200, resp.text
    assert store.close_owed_wake_for_holder("holder-12286") == 0


def test_attended_binding_from_resolve_registration_ids_list() -> None:
    from services.git_integration_worker.cse_session_holders import (
        _import_cse_session_warm_module,
    )

    warm = _import_cse_session_warm_module()
    reg = "f4ebcdea-1111-2222-3333-444444444444"
    binding = warm._attended_binding_from_resolve(
        {
            "current": {
                "registration_ids": [reg],
                "execution_id": "exec-1",
            },
            "seat_holder": {"registration_id": reg},
        }
    )
    assert binding is not None
    assert binding["registration_id"] == reg
    assert binding["execution_id"] == "exec-1"
