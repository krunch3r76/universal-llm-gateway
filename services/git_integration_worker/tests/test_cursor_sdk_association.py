"""Tests for GIW dispatch association topic extraction."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_association import (
    build_dispatch_association_fields,
    extract_dispatch_topic,
)
from services.git_integration_worker.git_worker_lifecycle_events import (
    request_id_from_dispatch_id,
    resolve_envelope_request_id,
)
from services.git_integration_worker.models.cursor_api import CursorDispatchRequest

_MINTED_DISPATCH_ID = "550e8400-e29b-41d4-a716-446655440000"


def test_extract_dispatch_topic_prefers_so_what() -> None:
    body = (
        "TYPE: DIRECTIVE\n"
        "so_what: ULG gains reliable closeout SMS\n"
        "intent: implement the board topic line\n"
    )
    assert extract_dispatch_topic(body) == "ULG gains reliable closeout SMS"


def test_extract_dispatch_topic_prefers_ulg_gain() -> None:
    body = "hello world\nulg_gain: operators see mission prose on the board\n"
    assert extract_dispatch_topic(body) == "operators see mission prose on the board"


def test_extract_dispatch_topic_skips_yaml_frontmatter() -> None:
    body = "---\npacket_kind: implement\n---\nso_what: real topic\n"
    assert extract_dispatch_topic(body) == "real topic"
    assert extract_dispatch_topic("---\n...\n---") is None


def test_build_dispatch_association_fields_prefers_explicit_request_id() -> None:
    req = CursorDispatchRequest(
        thread_id="21826",
        model="cursor/composer-2.5",
        dispatch_id=_MINTED_DISPATCH_ID,
        execution_id="exec-21826",
        request_id="rid",
        message="so_what: carry request id",
        caller_agent="dispatch",
    )
    fields = build_dispatch_association_fields(req=req, packet_text=req.message or "")
    assert fields["story_id"] == "rid"


def test_resolve_envelope_request_id_falls_back_to_dispatch_derivation() -> None:
    dispatch_id = "req-legacy-abc12345"
    assert resolve_envelope_request_id(dispatch_id=dispatch_id) == "req-legacy"
    assert request_id_from_dispatch_id(_MINTED_DISPATCH_ID) == _MINTED_DISPATCH_ID


def test_extract_dispatch_topic_caps_at_160() -> None:
    long = "word " * 80
    topic = extract_dispatch_topic(f"so_what: {long}")
    assert topic is not None
    assert len(topic) <= 161
    assert topic.endswith("…")
