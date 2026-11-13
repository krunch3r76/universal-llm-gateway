"""Houseless CDP generate admit wire (422 paths)."""

from __future__ import annotations

import pytest
from agent_bus_store.db import create_thread, init_db
from agent_bus_store.houseless_mailbox_thread import resolve_mailbox_thread_at_admit
from systems.frontier_consult.admission import FrontierEndpointError
from systems.frontier_consult.cdp_generate import apply_houseless_purpose_generate_admit

pytestmark = pytest.mark.offline


def test_mailbox_thread_required_raises() -> None:
    from agent_bus_store.houseless_mailbox_thread import MailboxThreadResolveError

    with pytest.raises(MailboxThreadResolveError) as exc:
        resolve_mailbox_thread_at_admit(
            mailbox_thread=None,
            caller_agent=None,
            thread_id=None,
        )
    assert exc.value.code == "mailbox_thread_required"


def test_houseless_parent_conflict_via_cdp_generate_admit() -> None:
    with pytest.raises(FrontierEndpointError) as exc:
        apply_houseless_purpose_generate_admit(
            request_id="req-parent-conflict",
            purpose="houseless",
            mission_kind=None,
            parent_thread="12345",
            mailbox_thread=None,
            caller_agent="cursor-sdk",
            provisional_thread="",
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "houseless_parent_conflict"
    assert exc.value.field == "parent_thread"


def test_mailbox_thread_resolve_error_maps_to_422_at_admit(
    tmp_path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="not-a-mailbox")
    assert detail is not None
    bad_id = str(detail["id"])
    with pytest.raises(FrontierEndpointError) as exc:
        apply_houseless_purpose_generate_admit(
            request_id="req-invalid-mailbox",
            purpose="houseless",
            mission_kind=None,
            parent_thread=None,
            mailbox_thread=bad_id,
            caller_agent="cursor-sdk",
            provisional_thread="",
        )
    assert exc.value.status_code == 422
    assert exc.value.code == "mailbox_thread_invalid"
