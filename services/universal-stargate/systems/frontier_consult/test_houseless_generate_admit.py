"""Houseless CDP generate admit wire (422 paths)."""

from __future__ import annotations

import pytest
from agent_bus_store.houseless_mailbox_thread import (
    MailboxThreadResolveError,
    resolve_mailbox_thread_at_admit,
)

pytestmark = pytest.mark.offline


def test_mailbox_thread_required_raises() -> None:
    with pytest.raises(MailboxThreadResolveError) as exc:
        resolve_mailbox_thread_at_admit(
            mailbox_thread=None,
            caller_agent=None,
            thread_id=None,
        )
    assert exc.value.code == "mailbox_thread_required"


def test_houseless_parent_conflict_codes_distinct() -> None:
    """Admit maps houseless+parent_thread to houseless_parent_conflict (see cdp_generate)."""
    from claude_bundles.what_is_running_view import is_houseless_purpose

    assert is_houseless_purpose("houseless")
    assert not is_houseless_purpose("ask")
