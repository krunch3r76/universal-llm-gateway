"""Hermetic tests for session-close digest payload derivation."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from cortex_store.digest_close_payload import (
    derive_payloads_from_entity_ids,
    dispatch_close_digests,
    is_valid_dated_journal_entity,
)
from cortex_store.digest_segment import Segment

_JOURNAL_TEXT = "# Health\n\nCalled the clinic.\n"
_ENTRY_DATE = "2026-07-14"


@pytest.mark.parametrize(
    ("entity_id", "expected"),
    [
        ("document:journal-entry-122", True),
        ("document:journal-2026-07-13", True),
        ("document:journal-bridge-spec", False),
        ("document:operator-journal-entry-1", False),
        ("todo:other", False),
    ],
)
def test_is_valid_dated_journal_entity(entity_id: str, expected: bool) -> None:
    assert is_valid_dated_journal_entity(entity_id) is expected


@pytest.mark.offline
def test_derive_payloads_skips_invalid_and_load_miss() -> None:
    with patch(
        "cortex_store.digest_close_payload.load_journal_entry_text",
        side_effect=lambda eid: (
            (_JOURNAL_TEXT, _ENTRY_DATE, "journal://entries/1")
            if eid == "document:journal-entry-1"
            else None
        ),
    ) as load_mock:
        payloads = derive_payloads_from_entity_ids(
            [
                "document:journal-bridge-spec",
                "document:journal-entry-1",
                "document:journal-entry-2",
            ]
        )
    assert load_mock.call_count == 2
    assert payloads
    assert payloads[0]["journal_entity_id"] == "document:journal-entry-1"


@pytest.mark.offline
def test_dispatch_close_digests_hook_off_no_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORTEX_DIGEST_CLOSE_HOOK", raising=False)
    segments = [
        Segment(
            entry_anchor=f"{_ENTRY_DATE}#health",
            heading="Health",
            entry_text="Called the clinic.",
        )
    ]
    with (
        patch(
            "cortex_store.digest_close_payload.derive_payloads_from_entity_ids",
            return_value=[
                {
                    "journal_entity_id": "document:journal-entry-1",
                    "entry_anchor": segments[0].entry_anchor,
                    "entry_text": segments[0].entry_text,
                }
            ],
        ),
        patch("cortex_store.digest_dispatch.threading.Thread") as thread_cls,
    ):
        dispatch_close_digests(
            entity_ids=["document:journal-entry-1"],
            explicit_digest=None,
            session_id="sess-1",
        )
    thread_cls.assert_not_called()


@pytest.mark.offline
def test_dispatch_close_digests_auto_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    auto = [
        {
            "journal_entity_id": "document:journal-entry-1",
            "entry_anchor": "2026-07-14#health",
            "entry_text": "section one",
        },
        {
            "journal_entity_id": "document:journal-entry-1",
            "entry_anchor": "2026-07-14#finance",
            "entry_text": "section two",
        },
    ]
    with (
        patch(
            "cortex_store.digest_close_payload.derive_payloads_from_entity_ids",
            return_value=auto,
        ),
        patch(
            "cortex_store.digest_close_payload.dispatch_digest_background"
        ) as dispatch_mock,
    ):
        dispatch_close_digests(
            entity_ids=["document:journal-entry-1"],
            explicit_digest=None,
            session_id="sess-auto",
        )
    assert dispatch_mock.call_count == 2


@pytest.mark.offline
def test_explicit_digest_wins_same_anchor_no_double_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    anchor = "2026-07-14#health"
    explicit: dict[str, Any] = {
        "journal_entity_id": "document:journal-entry-1",
        "entry_anchor": anchor,
        "entry_text": "caller text wins",
    }
    auto = [
        {
            "journal_entity_id": "document:journal-entry-1",
            "entry_anchor": anchor,
            "entry_text": "auto text",
        },
        {
            "journal_entity_id": "document:journal-entry-1",
            "entry_anchor": "2026-07-14#other",
            "entry_text": "other section",
        },
    ]
    with (
        patch(
            "cortex_store.digest_close_payload.derive_payloads_from_entity_ids",
            return_value=auto,
        ),
        patch(
            "cortex_store.digest_close_payload.dispatch_digest_background"
        ) as dispatch_mock,
    ):
        dispatch_close_digests(
            entity_ids=["document:journal-entry-1"],
            explicit_digest=explicit,
            session_id="sess-merge",
        )
    assert dispatch_mock.call_count == 2
    calls = [call.args[0] for call in dispatch_mock.call_args_list]
    anchor_calls = [c for c in calls if c.get("entry_anchor") == anchor]
    assert len(anchor_calls) == 1
    assert anchor_calls[0]["entry_text"] == "caller text wins"


@pytest.mark.offline
def test_dispatch_close_digests_fail_open_on_derive_exception() -> None:
    with patch(
        "cortex_store.digest_close_payload.derive_payloads_from_entity_ids",
        side_effect=RuntimeError("boom"),
    ):
        dispatch_close_digests(
            entity_ids=["document:journal-entry-1"],
            explicit_digest=None,
            session_id="sess-fail",
        )
