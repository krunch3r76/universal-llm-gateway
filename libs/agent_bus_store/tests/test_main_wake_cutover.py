"""Main-wake journal authority for house 21207 (chatgpt_work CAS head)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import init_db
from agent_bus_store.db.endpoint_subscriptions import (
    ChatgptWorkCasError,
    insert_main_wake_row_for_test,
    main_wake_latest,
)
from agent_bus_store.db.endpoint_wake_ledger import (
    resolve_orion_chat_wake,
    resolve_wake_delivery_binding,
)
from bus_watch.endpoint_wake_dispatcher import _orion_synthetic_route
from web_chat_relay.orion_wake import (
    PINNED_CONVERSATION_ID,
    holder_conversation_url,
)

pytestmark = pytest.mark.offline

_ORION_HOUSE = "21207"
_SYN_MAIN_OLD = (
    "https://chatgpt.com/g/g-p-proj-synth/c/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
)
_SYN_MAIN_NEW = (
    "https://chatgpt.com/g/g-p-proj-synth/c/bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
)
_SYN_MAIN_NEW_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


@pytest.fixture()
def db(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    monkeypatch.delenv("AGENT_BUS_ENDPOINT_WAKE_DISPATCHER", raising=False)
    init_db()


def test_absent_head_keeps_pin(db: None) -> None:
    binding = resolve_orion_chat_wake(_ORION_HOUSE)
    assert binding["state"] == "bound"
    assert binding["basis"] == "orion_pinned_conversation"
    assert binding["current"]["actuation"]["conversation_id"] == PINNED_CONVERSATION_ID


def test_terminal_head_fail_closed(db: None) -> None:
    insert_main_wake_row_for_test(conversation_url=_SYN_MAIN_NEW, state="subscribed")
    insert_main_wake_row_for_test(
        conversation_url=_SYN_MAIN_OLD,
        state="unsubscribed",
    )
    binding = resolve_orion_chat_wake(_ORION_HOUSE)
    assert binding["state"] == "unbound"
    assert binding.get("reason") == "terminal_head"


def test_subscribed_main_head_routes(db: None) -> None:
    insert_main_wake_row_for_test(conversation_url=_SYN_MAIN_NEW, state="subscribed")
    binding = resolve_orion_chat_wake(_ORION_HOUSE)
    assert binding["basis"] == "chatgpt_work"
    assert binding["current"]["actuation"]["conversation_url"] == _SYN_MAIN_NEW


def test_three_consumers_same_url(db: None) -> None:
    insert_main_wake_row_for_test(conversation_url=_SYN_MAIN_NEW, state="subscribed")
    binding = resolve_wake_delivery_binding(house_id=_ORION_HOUSE)
    route = _orion_synthetic_route("orion", _ORION_HOUSE)
    assert route is not None
    _, sub = route
    url = holder_conversation_url(sub)
    assert url == _SYN_MAIN_NEW
    assert binding["current"]["actuation"]["conversation_url"] == _SYN_MAIN_NEW

    from agent_bus_store.db.orion_wake_owner_adapter import _endpoint_from_actuation

    actuation = binding["current"]["actuation"]
    assert _endpoint_from_actuation(actuation) == _SYN_MAIN_NEW


def test_other_houses_stay_unbound(db: None) -> None:
    insert_main_wake_row_for_test(conversation_url=_SYN_MAIN_NEW, state="subscribed")
    for house in ("910001", "910002"):
        out = resolve_orion_chat_wake(house, agent="orion")
        assert out["state"] == "unbound"


def test_cas_signature_is_path_only(db: None, tmp_path) -> None:
    from agent_bus_store.db.endpoint_subscriptions import cas_main_wake_head

    bad = tmp_path / "target.md"
    bad.write_text("expected_old_url=x\nnew_url=y\n", encoding="utf-8")
    with pytest.raises(ChatgptWorkCasError) as exc:
        cas_main_wake_head(bad)
    assert exc.value.code == "target_refused"


def test_interim_main_not_pin_regression(db: None) -> None:
    """AC12: subscribed main head must not fall back to compiled pin."""
    insert_main_wake_row_for_test(conversation_url=_SYN_MAIN_NEW, state="subscribed")
    binding = resolve_orion_chat_wake(_ORION_HOUSE)
    assert binding["current"]["actuation"]["conversation_id"] == _SYN_MAIN_NEW_ID
    assert binding["current"]["actuation"]["conversation_id"] != PINNED_CONVERSATION_ID


def test_main_terminal_malformed_never_pin(db: None) -> None:
    """AC13: malformed newest main row is fail-closed, not pin."""
    from agent_bus_store.db.connection import write_connect

    with write_connect() as conn:
        conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, state, surface, display, title, agent_id, "
            "bound_by, evidence, scope_kind, actuation, home_house_id) "
            "VALUES ('main-wake','main-wake','subscribed',NULL,NULL,'not-a-url',"
            "'bad-id','test','test','agent','chatgpt_work',NULL)"
        )
    binding = resolve_orion_chat_wake(_ORION_HOUSE)
    assert binding["state"] == "unbound"
    assert binding.get("reason") == "terminal_head"
    binding = resolve_wake_delivery_binding(house_id=_ORION_HOUSE)
    assert binding["state"] == "unbound"
    assert binding.get("reason") == "terminal_head"
    assert binding.get("current") is None or binding["current"] is None
    assert main_wake_latest() is not None


def test_terminal_journal_blocks_legacy_orion_subscription(db: None) -> None:
    """Stop-token: terminal journal head must beat a live legacy orion row."""
    from agent_bus_store.db.connection import write_connect

    insert_main_wake_row_for_test(
        conversation_url=_SYN_MAIN_NEW,
        state="unsubscribed",
    )
    with write_connect() as conn:
        conn.execute(
            "INSERT INTO agent_endpoint_subscriptions "
            "(agent, thread_scope, agent_id, state, surface, display, title) "
            "VALUES ('orion', ?, ?, 'subscribed', 'cursor', '12', ?)",
            (_ORION_HOUSE, _SYN_MAIN_NEW_ID, _SYN_MAIN_NEW),
        )
    binding = resolve_wake_delivery_binding(house_id=_ORION_HOUSE)
    assert binding["state"] == "unbound"
    assert binding.get("reason") == "terminal_head"


def test_migration_023_copies_legacy_rows(db: None) -> None:
    from agent_bus_store.db import create_thread
    from agent_bus_store.db.connection import connect
    from agent_bus_store.db.endpoint_subscriptions import subscribe_endpoint

    create_thread(thread_id="910099", slug="legacy")
    subscribe_endpoint(
        agent="cursor",
        thread_scope="910099",
        surface="cursor",
        display=":8",
        title="legacy",
        agent_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    )
    with connect() as conn:
        row = conn.execute(
            "SELECT actuation FROM agent_endpoint_subscriptions WHERE thread_scope=?",
            ("910099",),
        ).fetchone()
    assert row["actuation"] is None
