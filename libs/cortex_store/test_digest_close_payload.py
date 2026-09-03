"""Hermetic tests for session-close digest payload derivation."""

from __future__ import annotations

import json
import urllib.error
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from cortex_store.digest_close_payload import (
    _read_journal_uri,
    derive_payloads_from_entity_ids,
    dispatch_close_digests,
    is_valid_dated_journal_entity,
)

_JOURNAL_TEXT = "# Health\n\nCalled the clinic.\n"
_ENTRY_DATE = "2026-07-14"
_ENTITY_ID = "document:journal-entry-1"
_JOURNAL_URI = "journal://entries/1"


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
def test_read_journal_uri_200_json_content(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOURNAL_BRIDGE_URL", "http://journal-bridge:8200")
    monkeypatch.setenv("BRIDGE_TOKEN", "bridge-secret")

    def _urlopen(req: Any, timeout: int = 5) -> MagicMock:
        assert req.get_header("Authorization") == "Bearer bridge-secret"
        body = json.dumps({"content": _JOURNAL_TEXT}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda *args: None
        return resp

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        assert _read_journal_uri(_JOURNAL_URI) == _JOURNAL_TEXT


@pytest.mark.offline
def test_read_journal_uri_401_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JOURNAL_BRIDGE_URL", "http://journal-bridge:8200")
    monkeypatch.setenv("BRIDGE_TOKEN", "bridge-secret")

    def _urlopen(_req: Any, timeout: int = 5) -> MagicMock:
        raise urllib.error.HTTPError(
            "http://journal-bridge:8200/entries/1",
            401,
            "Unauthorized",
            {},
            None,
        )

    with patch("urllib.request.urlopen", side_effect=_urlopen):
        assert _read_journal_uri(_JOURNAL_URI) is None


@pytest.mark.offline
def test_read_journal_uri_unset_bridge_url_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("JOURNAL_BRIDGE_URL", raising=False)
    with patch("urllib.request.urlopen") as urlopen_mock:
        assert _read_journal_uri(_JOURNAL_URI) is None
    urlopen_mock.assert_not_called()


@contextmanager
def _fake_cortex_conn():
    yield MagicMock()


def _patch_journal_entity(entity_id: str, source_uri: str):
    return patch.multiple(
        "cortex_store.digest_close_payload",
        cortex_conn=_fake_cortex_conn,
        query=lambda _conn, _sql, _params: [{"id": entity_id}],
        decode_row=lambda _row, json_fields: {
            "id": entity_id,
            "source_uri": source_uri,
            "name": entity_id,
            "attributes": {},
        },
    )


@pytest.mark.offline
def test_derive_payloads_skips_invalid_and_load_miss() -> None:
    with patch(
        "cortex_store.digest_close_payload.load_journal_entry_text",
        side_effect=lambda eid: (
            (_JOURNAL_TEXT, _ENTRY_DATE, _JOURNAL_URI)
            if eid == _ENTITY_ID
            else None
        ),
    ) as load_mock:
        payloads = derive_payloads_from_entity_ids(
            [
                "document:journal-bridge-spec",
                _ENTITY_ID,
                "document:journal-entry-2",
            ]
        )
    assert load_mock.call_count == 2
    assert payloads
    assert payloads[0]["journal_entity_id"] == _ENTITY_ID


@pytest.mark.offline
def test_derive_payloads_from_journal_bridge_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JOURNAL_BRIDGE_URL", "http://journal-bridge:8200")
    monkeypatch.setenv("BRIDGE_TOKEN", "bridge-secret")

    def _urlopen(_req: Any, timeout: int = 5) -> MagicMock:
        body = json.dumps({"content": _JOURNAL_TEXT}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda *args: None
        return resp

    with (
        _patch_journal_entity(_ENTITY_ID, _JOURNAL_URI),
        patch("urllib.request.urlopen", side_effect=_urlopen),
    ):
        payloads = derive_payloads_from_entity_ids([_ENTITY_ID])

    assert payloads
    assert payloads[0]["journal_entity_id"] == _ENTITY_ID
    assert payloads[0]["entry_text"]


@pytest.mark.offline
def test_dispatch_close_digests_hook_off_no_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CORTEX_DIGEST_CLOSE_HOOK", raising=False)
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _capture_record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with (
        patch(
            "cortex_store.digest_close_payload.derive_payloads_from_entity_ids"
        ) as derive_mock,
        patch(
            "cortex_store.digest_close_payload.dispatch_digest_background"
        ) as dispatch_mock,
        patch(
            "cortex_store.dispatch_ops._shared.record",
            side_effect=_capture_record,
        ),
    ):
        dispatch_close_digests(
            entity_ids=[_ENTITY_ID],
            explicit_digest=None,
            session_id="sess-1",
        )
    derive_mock.assert_not_called()
    dispatch_mock.assert_not_called()
    assert recorded == [
        (
            "cortex.digest.close_payload",
            {
                "session_id": "sess-1",
                "derived_count": 0,
                "enqueued_count": 0,
                "skipped": 1,
                "reason": "hook_disabled",
            },
        )
    ]


@pytest.mark.offline
def test_dispatch_close_digests_auto_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    auto = [
        {
            "journal_entity_id": _ENTITY_ID,
            "entry_anchor": "2026-07-14#health",
            "entry_text": "section one",
        },
        {
            "journal_entity_id": _ENTITY_ID,
            "entry_anchor": "2026-07-14#finance",
            "entry_text": "section two",
        },
    ]
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _capture_record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with (
        patch(
            "cortex_store.digest_close_payload.derive_payloads_from_entity_ids",
            return_value=auto,
        ),
        patch(
            "cortex_store.digest_close_payload.dispatch_digest_background"
        ) as dispatch_mock,
        patch(
            "cortex_store.dispatch_ops._shared.record",
            side_effect=_capture_record,
        ),
    ):
        dispatch_close_digests(
            entity_ids=[_ENTITY_ID],
            explicit_digest=None,
            session_id="sess-auto",
        )
    assert dispatch_mock.call_count == 2
    assert recorded[-1] == (
        "cortex.digest.close_payload",
        {
            "session_id": "sess-auto",
            "derived_count": 2,
            "enqueued_count": 2,
            "skipped": 0,
            "reason": "",
        },
    )


@pytest.mark.offline
def test_dispatch_close_digests_hook_on_bridge_200_enqueues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    monkeypatch.setenv("JOURNAL_BRIDGE_URL", "http://journal-bridge:8200")
    monkeypatch.setenv("BRIDGE_TOKEN", "bridge-secret")

    def _urlopen(_req: Any, timeout: int = 5) -> MagicMock:
        body = json.dumps({"content": _JOURNAL_TEXT}).encode()
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda self: resp
        resp.__exit__ = lambda *args: None
        return resp

    with (
        _patch_journal_entity(_ENTITY_ID, _JOURNAL_URI),
        patch("urllib.request.urlopen", side_effect=_urlopen),
        patch(
            "cortex_store.digest_close_payload.dispatch_digest_background"
        ) as dispatch_mock,
        patch("cortex_store.dispatch_ops._shared.record"),
    ):
        dispatch_close_digests(
            entity_ids=[_ENTITY_ID],
            explicit_digest=None,
            session_id="sess-bridge",
        )
    assert dispatch_mock.call_count >= 1


@pytest.mark.offline
def test_explicit_digest_wins_same_anchor_no_double_enqueue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    anchor = "2026-07-14#health"
    explicit: dict[str, Any] = {
        "journal_entity_id": _ENTITY_ID,
        "entry_anchor": anchor,
        "entry_text": "caller text wins",
    }
    auto = [
        {
            "journal_entity_id": _ENTITY_ID,
            "entry_anchor": anchor,
            "entry_text": "auto text",
        },
        {
            "journal_entity_id": _ENTITY_ID,
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
        patch("cortex_store.dispatch_ops._shared.record"),
    ):
        dispatch_close_digests(
            entity_ids=[_ENTITY_ID],
            explicit_digest=explicit,
            session_id="sess-merge",
        )
    assert dispatch_mock.call_count == 2
    calls = [call.args[0] for call in dispatch_mock.call_args_list]
    anchor_calls = [c for c in calls if c.get("entry_anchor") == anchor]
    assert len(anchor_calls) == 1
    assert anchor_calls[0]["entry_text"] == "caller text wins"


@pytest.mark.offline
def test_dispatch_close_digests_fail_open_on_derive_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CORTEX_DIGEST_CLOSE_HOOK", "1")
    recorded: list[tuple[str, dict[str, Any]]] = []

    def _capture_record(signal: str, **payload: Any) -> None:
        recorded.append((signal, payload))

    with (
        patch(
            "cortex_store.digest_close_payload.derive_payloads_from_entity_ids",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "cortex_store.dispatch_ops._shared.record",
            side_effect=_capture_record,
        ),
    ):
        dispatch_close_digests(
            entity_ids=[_ENTITY_ID],
            explicit_digest=None,
            session_id="sess-fail",
        )
    assert recorded[-1] == (
        "cortex.digest.close_payload",
        {
            "session_id": "sess-fail",
            "derived_count": 0,
            "enqueued_count": 0,
            "skipped": 0,
            "reason": "exception",
        },
    )
