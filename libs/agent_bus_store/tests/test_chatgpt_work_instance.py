"""Instance-scope chatgpt_work heads (satellite houses)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db, insert_turn
from agent_bus_store.db.connection import connect
from agent_bus_store.db.endpoint_subscriptions import (
    ChatgptWorkCasError,
    insert_chatgpt_work_row_for_test,
)
from agent_bus_store.db.endpoint_wake_ledger import (
    _latest_orion_seat_registration_on_thread,
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
from bus_watch.endpoint_wake_dispatcher import _chatgpt_work_route, resolve_target

pytestmark = pytest.mark.offline

_HOME_A = "910001"
_HOME_B = "910002"
_INST_A = "inst-a"
_INST_B = "inst-b"
_URL_A = (
    "https://chatgpt.com/g/g-p-proj-a/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
)
_URL_MAIN_ECHO = (
    "https://chatgpt.com/g/g-p-proj-b/c/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)
_CONV_MAIN = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def _seat_registration_body(url: str) -> str:
    return f"TYPE: SEAT_REGISTRATION\nNew exact URL: {url}\n"


def test_21342_resolves_instance_not_main(db: None) -> None:
    """AC1: instance journal row wins over bus seat registration text."""
    create_thread(thread_id=_HOME_A, slug="sat-a")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
    )
    insert_turn(
        thread=_HOME_A,
        from_agent="foreign",
        to_agent="orion",
        subject="SEAT_REGISTRATION|echo",
        body=_seat_registration_body(_URL_MAIN_ECHO),
    )
    binding = resolve_orion_chat_wake(_HOME_A, agent="orion")
    assert binding["state"] == "bound"
    assert binding["basis"] == "chatgpt_work"
    assert binding["current"]["actuation"]["conversation_url"] == _URL_A


def test_untrusted_seat_registration_ignored(db: None) -> None:
    """AC2: without journal row, seat registration does not route."""
    create_thread(thread_id=_HOME_A, slug="sat-a")
    insert_turn(
        thread=_HOME_A,
        from_agent="foreign",
        to_agent="orion",
        subject="SEAT_REGISTRATION|echo",
        body=_seat_registration_body(_URL_MAIN_ECHO),
    )
    assert resolve_orion_chat_wake(_HOME_A, agent="orion")["state"] == "unbound"
    assert _latest_orion_seat_registration_on_thread(_HOME_A) is not None


def test_stale_url_refused(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC3: CAS conflict when expected_old_url does not match head."""
    create_thread(thread_id=_HOME_A, slug="sat-a")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
    )
    from agent_bus_store.db import endpoint_subscriptions as subs

    monkeypatch.setattr(subs, "_file_sha256", lambda _p: subs.INSTANCE_TARGET_SHA256)
    with pytest.raises(ChatgptWorkCasError) as exc:
        from pathlib import Path

        target = Path("/tmp/synth-instance-target.md")
        target.write_text(
            "instance_id=inst-a\n"
            f"home_house_id={_HOME_A}\n"
            "expected_old_url=ABSENT\n"
            f"new_url={_URL_MAIN_ECHO}\n"
            "attempt_id=test\n"
            "handoff_sha256=abc\n",
            encoding="utf-8",
        )
        subs.cas_instance_wake_head(target)
    assert exc.value.code == "seat_cas_conflict"


def test_terminal_head_fail_closed_instance(db: None) -> None:
    """AC4: newest instance row tombstone → unbound."""
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
        state="subscribed",
    )
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_MAIN_ECHO,
        state="unsubscribed",
    )
    out = resolve_orion_chat_wake(_HOME_A, agent="orion")
    assert out["state"] == "unbound"
    assert out.get("reason") == "terminal_head"


def test_housing_unbound(db: None) -> None:
    """AC6 / AC7: unrelated house stays unbound."""
    assert resolve_orion_chat_wake("910099", agent="orion")["state"] == "unbound"


def test_three_consumer_parity(db: None) -> None:
    """AC8: resolve, dispatcher, claim agree on instance URL."""
    create_thread(thread_id=_HOME_A, slug="sat-a")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
    )
    turn_id, _ts, number, _c = insert_turn(
        thread=_HOME_A,
        from_agent="maestro",
        to_agent="orion",
        subject="REQUEST|x",
        body="b",
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
                "thread_id": _HOME_A,
                "turn_id": turn_id,
                "turn_number": number,
            }
        ],
        now="2020-01-01 00:00:00",
    )
    claimed = claim_due_wakes(now="2020-01-01 00:00:30")
    wake_id = str(claimed["wake_id"])
    assert wake_id

    binding = resolve_wake_delivery_binding(house_id=_HOME_A, agent="orion")
    route = _chatgpt_work_route("orion", _HOME_A)
    assert route is not None
    _, sub = route
    assert sub["title"] == _URL_A
    assert binding["current"]["actuation"]["conversation_url"] == _URL_A

    from agent_bus_store.db.orion_wake_owner_adapter import _endpoint_from_actuation

    assert _endpoint_from_actuation(binding["current"]["actuation"]) == _URL_A

    owner = shared_wake_delivery_owner()
    claim = WakeClaimContext(
        wake_id=wake_id,
        thread_id=_HOME_A,
        agent="orion",
        endpoint_url=_URL_A,
        turn_ids=(turn_id,),
        turn_numbers=(number,),
    )
    assert owner.validate_claim_for_delivery(claim) == {
        "ok": True,
        "reason": "claim_valid",
    }


def test_agent_required(db: None) -> None:
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
    )
    assert resolve_orion_chat_wake(_HOME_A)["state"] == "unbound"
    assert resolve_orion_chat_wake(_HOME_A, agent="orion")["state"] == "bound"


def test_absent_cas_allows_retired_instance_on_house(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Newest-row ABSENT: historic subscribed row with newer tombstone does not block."""
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
        state="subscribed",
    )
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_MAIN_ECHO,
        state="unsubscribed",
    )
    from agent_bus_store.db import endpoint_subscriptions as subs

    monkeypatch.setattr(subs, "_file_sha256", lambda _p: subs.INSTANCE_TARGET_SHA256)
    from pathlib import Path

    target = Path("/tmp/synth-instance-absent-retired.md")
    target.write_text(
        f"instance_id={_INST_B}\n"
        f"home_house_id={_HOME_A}\n"
        "expected_old_url=ABSENT\n"
        f"new_url={_URL_MAIN_ECHO}\n"
        "attempt_id=test-absent\n"
        "handoff_sha256=ghi\n",
        encoding="utf-8",
    )
    out = subs.cas_instance_wake_head(target)
    assert out["appended"] is True


def test_cas_target_missing_raises_target_refused(db: None) -> None:
    from pathlib import Path

    from agent_bus_store.db.endpoint_subscriptions import cas_instance_wake_head

    missing = Path("/tmp/does-not-exist-chatgpt-work-target.md")
    with pytest.raises(ChatgptWorkCasError) as exc:
        cas_instance_wake_head(missing)
    assert exc.value.code == "target_refused"


def test_parse_target_no_blank_line_elision(db: None, tmp_path) -> None:
    from agent_bus_store.db.endpoint_subscriptions import _parse_target_lines

    path = tmp_path / "target.md"
    path.write_text(
        "expected_old_url=ABSENT\n\nnew_url=https://chatgpt.com/g/x/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\n",
        encoding="utf-8",
    )
    parsed = _parse_target_lines(
        path, frozenset({"expected_old_url", "new_url"})
    )
    assert parsed["expected_old_url"] == "ABSENT"


def test_instance_bypass_blocked(db: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC14: second instance cannot CAS from ABSENT when house already has a head."""
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
    )
    from agent_bus_store.db import endpoint_subscriptions as subs

    monkeypatch.setattr(subs, "_file_sha256", lambda _p: subs.INSTANCE_TARGET_SHA256)
    with pytest.raises(ChatgptWorkCasError) as exc:
        from pathlib import Path

        target = Path("/tmp/synth-instance-bypass.md")
        target.write_text(
            f"instance_id={_INST_B}\n"
            f"home_house_id={_HOME_A}\n"
            "expected_old_url=ABSENT\n"
            f"new_url={_URL_MAIN_ECHO}\n"
            "attempt_id=test2\n"
            "handoff_sha256=def\n",
            encoding="utf-8",
        )
        subs.cas_instance_wake_head(target)
    assert exc.value.code == "seat_cas_conflict"


def test_dispatcher_resolves_instance(db: None) -> None:
    create_thread(thread_id=_HOME_A, slug="sat-a")
    insert_chatgpt_work_row_for_test(
        instance_id=_INST_A,
        home_house_id=_HOME_A,
        conversation_url=_URL_A,
    )

    class Store:
        def subscription(self, agent: str, thread_id: str, raw_to=None):
            return None

    route, row = resolve_target(Store(), "orion", _HOME_A)  # type: ignore[arg-type]
    assert route == "exact"
    assert row["title"] == _URL_A
