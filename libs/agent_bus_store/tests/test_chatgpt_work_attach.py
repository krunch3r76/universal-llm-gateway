"""Production attach_instance_wake_head and POST /endpoint-subscriptions chatgpt_work."""

from __future__ import annotations

import threading

import pytest
from agent_bus_store.auth import require_token
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import (
    ChatgptWorkCasError,
    attach_instance_wake_head,
    insert_chatgpt_work_row_for_test,
    insert_main_wake_row_for_test,
)
from agent_bus_store.db.endpoint_wake_ledger import (
    advance_scan,
    claim_due_wakes,
    get_cursor,
    resolve_orion_chat_wake,
    resolve_wake_delivery_binding,
)
from agent_bus_store.db.orion_wake_owner_adapter import (
    WakeClaimContext,
    shared_wake_delivery_owner,
)
from agent_bus_store.server import create_app
from bus_watch.endpoint_wake_dispatcher import _chatgpt_work_route, resolve_target
from fastapi.testclient import TestClient
from web_chat_relay.orion_wake import PINNED_CONVERSATION_URL

pytestmark = pytest.mark.offline

_HOME = "22388"
_CHILD = "22394"
_INST = f"orion-{_HOME}"
_URL = (
    "https://chatgpt.com/g/g-p-6ae3debfee8481918ff3fa45b7d18d3a-ulg-orion/c/"
    "6aecb6b3-4a8c-83e8-8d72-cf08d91e5f7e"
)
_CONV = "6aecb6b3-4a8c-83e8-8d72-cf08d91e5f7e"
_URL_B = "https://chatgpt.com/g/g-p-other-proj/c/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_HANDOFF = "9ec534b690e3fc54af0d7d7bad25989a1c5b196d1486ff65182e15dd796f1498"
_BOUND_BY = "seat:orion@22388"
_EVIDENCE = (
    "target=runtime:01a1aca9-b2f5-7210-9dca-3bb405fc9038;dom:a42599;operator=kaywan"
)
_OTHER_HOME = "22387"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()
    create_thread(thread_id=_HOME, slug="orion-coordinator")
    create_thread(thread_id=_CHILD, slug="dispatch-child")


@pytest.fixture()
def client(db: None, tmp_path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app()
    app.dependency_overrides[require_token] = lambda: None
    return TestClient(app)


def _attach(**overrides: object) -> dict[str, object]:
    payload = {
        "home_house_id": _HOME,
        "conversation_url": _URL,
        "expected": "ABSENT",
        "attempt_id": "attempt-1",
        "handoff_sha256": _HANDOFF,
        "bound_by": _BOUND_BY,
        "evidence": _EVIDENCE,
    }
    payload.update(overrides)
    return attach_instance_wake_head(**payload)  # type: ignore[arg-type]


def _post_work(client: TestClient, **overrides: object) -> object:
    body = {
        "agent": "orion",
        "thread_scope": _HOME,
        "actuation": "chatgpt_work",
        "home_house_id": _HOME,
        "conversation_url": _URL,
        "expected": "ABSENT",
        "attempt_id": "attempt-http-1",
        "handoff_sha256": _HANDOFF,
        "bound_by": _BOUND_BY,
        "evidence": _EVIDENCE,
    }
    body.update(overrides)
    return client.post("/endpoint-subscriptions", json=body)


def test_ac1_attach_absent_resolves_bound(db: None) -> None:
    out = _attach()
    row = out["row"]
    assert out["appended"] is True
    assert row["actuation"] == "chatgpt_work"
    assert row["home_house_id"] == _HOME
    assert row["title"] == _URL
    assert row["agent_id"] == _CONV
    assert row["bound_by"] == _BOUND_BY
    binding = resolve_orion_chat_wake(_HOME, agent="orion")
    assert binding["state"] == "bound"
    assert binding["basis"] == "chatgpt_work"
    assert binding["current"]["actuation"]["conversation_url"] == _URL


def test_ac2_idempotent_replay(db: None) -> None:
    first = _attach()
    second = _attach()
    assert first["appended"] is True
    assert second["appended"] is False
    assert second.get("idempotent_replay") is True
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_endpoint_subscriptions WHERE actuation='chatgpt_work'"
        ).fetchone()[0]
    assert count == 1


def test_ac3_replay_absent_different_url_conflict(db: None) -> None:
    _attach()
    with pytest.raises(ChatgptWorkCasError) as exc:
        _attach(conversation_url=_URL_B, attempt_id="attempt-2")
    assert exc.value.code == "seat_cas_conflict"


def test_ac4_concurrent_absent_one_wins(db: None) -> None:
    barrier = threading.Barrier(2)
    results: list[dict[str, object]] = []
    errors: list[ChatgptWorkCasError] = []

    def worker(attempt: str) -> None:
        barrier.wait()
        try:
            results.append(
                _attach(
                    attempt_id=attempt,
                    conversation_url=_URL if attempt == "a" else _URL_B,
                )
            )
        except ChatgptWorkCasError as exc:
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("a",))
    t2 = threading.Thread(target=worker, args=("b",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()
    assert len(results) == 1
    assert len(errors) == 1
    assert errors[0].code == "seat_cas_conflict"
    with connect() as conn:
        subscribed = conn.execute(
            "SELECT COUNT(*) FROM agent_endpoint_subscriptions "
            "WHERE actuation='chatgpt_work' AND state='subscribed'"
        ).fetchone()[0]
    assert subscribed == 1


def test_ac5_expected_head_cas(db: None) -> None:
    first = _attach()
    head_id = int(first["row"]["id"])
    with pytest.raises(ChatgptWorkCasError) as stale:
        attach_instance_wake_head(
            home_house_id=_HOME,
            conversation_url=_URL_B,
            expected=f"head:{head_id + 999}",
            attempt_id="attempt-replace",
            handoff_sha256=_HANDOFF,
            bound_by=_BOUND_BY,
            evidence=_EVIDENCE,
        )
    assert stale.value.code == "seat_cas_conflict"
    replaced = attach_instance_wake_head(
        home_house_id=_HOME,
        conversation_url=_URL_B,
        expected=f"head:{head_id}",
        attempt_id="attempt-replace",
        handoff_sha256=_HANDOFF,
        bound_by=_BOUND_BY,
        evidence=_EVIDENCE,
    )
    assert replaced["appended"] is True
    assert replaced["row"]["title"] == _URL_B
    assert replaced["row"]["predecessor_id"] == head_id
    with connect() as conn:
        tomb = conn.execute(
            "SELECT state FROM agent_endpoint_subscriptions "
            "WHERE id > ? AND agent=? AND state='unsubscribed' "
            "ORDER BY id ASC LIMIT 1",
            (head_id, _INST),
        ).fetchone()
    assert tomb is not None
    binding = resolve_orion_chat_wake(_HOME, agent="orion")
    assert binding["current"]["actuation"]["conversation_url"] == _URL_B


def test_ac6_other_houses_and_main_unchanged(db: None) -> None:
    main_url = PINNED_CONVERSATION_URL.split("?", 1)[0]
    insert_main_wake_row_for_test(conversation_url=main_url)
    insert_chatgpt_work_row_for_test(
        instance_id="orion-other",
        home_house_id=_OTHER_HOME,
        conversation_url=_URL_B,
    )
    with connect() as conn:
        before = conn.execute(
            "SELECT id, title, state FROM agent_endpoint_subscriptions "
            "WHERE actuation='chatgpt_work' ORDER BY id"
        ).fetchall()
    _attach()
    with connect() as conn:
        after = conn.execute(
            "SELECT id, title, state FROM agent_endpoint_subscriptions "
            "WHERE actuation='chatgpt_work' ORDER BY id"
        ).fetchall()
    assert len(after) == len(before) + 1
    assert [dict(r) for r in before] == [dict(r) for r in after[: len(before)]]


def test_ac7_conversation_already_bound(db: None) -> None:
    insert_chatgpt_work_row_for_test(
        instance_id="orion-other",
        home_house_id=_OTHER_HOME,
        conversation_url=_URL,
    )
    with pytest.raises(ChatgptWorkCasError) as exc:
        _attach()
    assert exc.value.code == "conversation_already_bound"


def test_ac8_validation_refusals(db: None) -> None:
    cases = [
        ({"conversation_url": "http://chatgpt.com/c/" + _CONV}, "work_url_refused"),
        (
            {"conversation_url": _URL + "?foo=1"},
            "work_url_refused",
        ),
        (
            {"conversation_url": "https://chatgpt.com/no-uuid-here"},
            "work_url_refused",
        ),
        (
            {
                "conversation_url": PINNED_CONVERSATION_URL,
            },
            "work_url_refused",
        ),
        ({"home_house_id": "missing-house"}, "endpoint_thread_missing"),
        ({"handoff_sha256": "short"}, "work_handoff_refused"),
        ({"instance_id": "main-wake"}, "work_instance_refused"),
        ({"bound_by": ""}, "work_bound_by_refused"),
        ({"evidence": ""}, "work_evidence_refused"),
    ]
    for overrides, code in cases:
        with pytest.raises(ChatgptWorkCasError) as exc:
            attach_instance_wake_head(
                home_house_id=str(overrides.pop("home_house_id", _HOME)),
                conversation_url=str(
                    overrides.pop(
                        "conversation_url",
                        _URL,
                    )
                ),
                expected="ABSENT",
                attempt_id="x",
                handoff_sha256=str(overrides.pop("handoff_sha256", _HANDOFF)),
                bound_by=str(overrides.pop("bound_by", _BOUND_BY)),
                evidence=str(overrides.pop("evidence", _EVIDENCE)),
                **overrides,
            )
        assert exc.value.code == code, overrides


def test_ac9_dispatcher_delivery_route(db: None) -> None:
    _attach()
    turn_id, _ts, number, _c = insert_turn(
        thread=_HOME,
        from_agent="coordinator",
        to_agent="orion",
        subject="READY_FOR_REVIEW|wake-attach",
        body="closeout",
    )
    get_cursor()
    with connect() as conn:
        conn.execute("UPDATE endpoint_wake_cursor SET high_water_turn_id=0")
    advance_scan(
        "endpoint-wake-dispatcher",
        0,
        turn_id,
        [
            {
                "agent": "orion",
                "thread_id": _HOME,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(claimed["wake_id"])
    route = _chatgpt_work_route("orion", _HOME)
    assert route is not None
    kind, sub = route
    assert kind == "exact"
    assert sub["title"] == _URL
    binding = resolve_wake_delivery_binding(house_id=_HOME, agent="orion")
    assert binding["current"]["actuation"]["conversation_url"] == _URL
    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id=wake_id,
        thread_id=_HOME,
        agent="orion",
        endpoint_url=_URL,
        turn_ids=(turn_id,),
        turn_numbers=(number,),
    )
    assert owner.validate_claim_for_delivery(claim)["ok"] is True

    from web_chat_relay.orion_wake import (
        _strip_conversation_url,
        conversation_id_from_url,
    )

    assert conversation_id_from_url(_URL) == _CONV
    assert _strip_conversation_url(_URL) == _URL
    assert wake_id


def test_ac10_generic_subscribe_unchanged(client: TestClient) -> None:
    thread = create_thread(thread_id=None, slug="generic-sub")["id"]
    resp = client.post(
        "/endpoint-subscriptions",
        json={
            "agent": "grok-bot-gc1",
            "thread_scope": str(thread),
            "surface": "grok",
            "display": "9",
            "title": "lane",
        },
    )
    assert resp.status_code == 200
    resp2 = client.post(
        "/endpoint-subscriptions",
        json={
            "agent": "cursor",
            "thread_scope": str(thread),
            "surface": "cursor",
            "display": "8",
        },
    )
    assert resp2.status_code == 422
    assert resp2.json()["detail"]["code"] == "endpoint_title_or_agent_required"


def test_ac11_mcp_subscribe_forwards_work_fields(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys
    from pathlib import Path

    mcp_root = Path(__file__).resolve().parents[3] / "services/mcp-server"
    if str(mcp_root) not in sys.path:
        sys.path.insert(0, str(mcp_root))
    from tools.agent_bus.subscriptions import _subscribe_dispatch

    captured: dict[str, object] = {}

    def fake_relay(
        _svc: str, _method: str, _path: str, *, body: dict[str, object]
    ) -> dict[str, object]:
        captured.update(body)
        return {"appended": True, "row": {"id": 1}}

    monkeypatch.setattr(
        "tools.agent_bus.subscriptions.relay",
        fake_relay,
    )
    _subscribe_dispatch(
        agent="orion",
        thread_scope=_HOME,
        actuation="chatgpt_work",
        home_house_id=_HOME,
        conversation_url=_URL,
        expected="ABSENT",
        attempt_id="mcp-1",
        handoff_sha256=_HANDOFF,
        bound_by=_BOUND_BY,
        evidence=_EVIDENCE,
    )
    assert captured["actuation"] == "chatgpt_work"
    assert captured["conversation_url"] == _URL
    assert "surface" not in captured


def test_ac12_child_thread_stays_unbound_after_parent_attach(db: None) -> None:
    _attach()
    assert resolve_orion_chat_wake(_HOME, agent="orion")["state"] == "bound"
    assert resolve_orion_chat_wake(_CHILD, agent="orion")["state"] == "unbound"


def test_http_attach_and_resolve_route(client: TestClient) -> None:
    resp = _post_work(client)
    assert resp.status_code == 200, resp.text
    resolve = client.get(
        "/delivery-bindings/resolve",
        params={"house_id": _HOME, "intent": "wake", "agent": "orion"},
    )
    assert resolve.status_code == 200
    body = resolve.json()
    assert body["state"] == "bound"
    assert body["basis"] == "chatgpt_work"
    assert body["current"]["actuation"]["conversation_url"] == _URL


def test_cas_conflict_http_409(client: TestClient) -> None:
    assert _post_work(client).status_code == 200
    conflict = _post_work(client, attempt_id="other", conversation_url=_URL_B)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "seat_cas_conflict"


def test_b1_scope_mismatch_rejects_before_write(client: TestClient) -> None:
    bad_agent = client.post(
        "/endpoint-subscriptions",
        json={
            "agent": "grok-bot-gc1",
            "thread_scope": _HOME,
            "actuation": "chatgpt_work",
            "home_house_id": _HOME,
            "conversation_url": _URL,
            "expected": "ABSENT",
            "attempt_id": "scope-1",
            "handoff_sha256": _HANDOFF,
            "bound_by": _BOUND_BY,
            "evidence": _EVIDENCE,
        },
    )
    assert bad_agent.status_code == 422
    assert bad_agent.json()["detail"]["code"] == "work_scope_mismatch"
    bad_house = client.post(
        "/endpoint-subscriptions",
        json={
            "agent": "orion",
            "thread_scope": _HOME,
            "actuation": "chatgpt_work",
            "home_house_id": _OTHER_HOME,
            "conversation_url": _URL,
            "expected": "ABSENT",
            "attempt_id": "scope-2",
            "handoff_sha256": _HANDOFF,
            "bound_by": _BOUND_BY,
            "evidence": _EVIDENCE,
        },
    )
    assert bad_house.status_code == 422
    assert bad_house.json()["detail"]["code"] == "work_scope_mismatch"
    with connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM agent_endpoint_subscriptions WHERE actuation='chatgpt_work'"
        ).fetchone()[0]
    assert count == 0


def test_b2_uppercase_url_conflicts_with_mixed_case_bound_row(db: None) -> None:
    upper_url = (
        "https://chatgpt.com/g/g-p-proj/c/6AECB6B3-4A8C-83E8-8D72-CF08D91E5F7E"
    )
    insert_chatgpt_work_row_for_test(
        instance_id="orion-other",
        home_house_id=_OTHER_HOME,
        conversation_url=upper_url,
    )
    with pytest.raises(ChatgptWorkCasError) as exc:
        _attach()
    assert exc.value.code == "conversation_already_bound"


def test_b2_main_wake_non_pinned_url_blocks_attach(db: None) -> None:
    insert_main_wake_row_for_test(conversation_url=_URL)
    with pytest.raises(ChatgptWorkCasError) as exc:
        _attach()
    assert exc.value.code == "conversation_already_bound"


def test_b2_project_path_uses_c_segment_not_g_uuid(db: None) -> None:
    ambiguous = (
        "https://chatgpt.com/g/6aecb6b3-4a8c-83e8-8d72-cf08d91e5f7e/c/"
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    )
    out = attach_instance_wake_head(
        home_house_id=_HOME,
        conversation_url=ambiguous,
        expected="ABSENT",
        attempt_id="path-segment",
        handoff_sha256=_HANDOFF,
        bound_by=_BOUND_BY,
        evidence=_EVIDENCE,
    )
    assert out["row"]["agent_id"] == "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_b3_caller_evidence_cannot_override_attempt_or_handoff(db: None) -> None:
    out = _attach(
        attempt_id="attempt-truth",
        evidence="attempt_id=forged;handoff_sha256=deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
    )
    stored = str(out["row"]["evidence"])
    assert stored.startswith("attempt_id=attempt-truth;handoff_sha256=" + _HANDOFF)
    assert "forged" not in stored.split(";", 2)[0]


def test_b3_same_attempt_different_payload_conflicts(db: None) -> None:
    _attach(attempt_id="dup-attempt")
    with pytest.raises(ChatgptWorkCasError) as exc:
        _attach(
            attempt_id="dup-attempt",
            evidence=_EVIDENCE + ";extra=touch",
        )
    assert exc.value.code == "seat_cas_conflict"


def test_closeout_subjects_qualify_wake_to_orion_on_22388(db: None) -> None:
    from bus_watch.endpoint_wake_dispatcher import qualify

    wake_subjects = (
        "CLOSEOUT|ORION-22388-BOUND-20261106-A",
        "cursor-sdk CLOSEOUT|lane-22394",
    )
    for subject in wake_subjects:
        turn = {
            "from_agent": "cursor-sdk",
            "to_agent": "orion",
            "thread": _HOME,
            "subject": subject,
        }
        verdict = qualify(turn, cse=False, store=None)
        assert verdict.wake is True, subject

    review_turn = {
        "from_agent": "cursor-sdk",
        "to_agent": "orion",
        "thread": _HOME,
        "subject": "READY_FOR_REVIEW|todo:mcp-session-self-location-wake-attach",
    }
    assert qualify(review_turn, cse=False, store=None).wake is False


def test_dispatcher_resolve_target_display_12(db: None) -> None:
    _attach()

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return None

    route, row = resolve_target(Store(), "orion", _HOME)  # type: ignore[arg-type]
    assert route == "exact"
    assert row["title"] == _URL
