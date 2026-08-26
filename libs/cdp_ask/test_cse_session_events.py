"""Event emission tests for cse_session satellite routes."""

from __future__ import annotations

from cdp_ask.cse_session_events import (
    emit,
    mcp_cse_session_acknowledged,
    mcp_cse_session_harvested,
    mcp_cse_session_pasted,
)


def test_paste_event_has_no_ack_class(monkeypatch) -> None:
    captured: list[str] = []
    monkeypatch.setattr(
        "cdp_ask.cse_session_events.socket.create_connection",
        lambda *a, **k: (_ for _ in ()).throw(OSError("skip tcp")),
    )
    monkeypatch.setattr(
        "cdp_ask.cse_session_events.socket.socket",
        lambda *a, **k: type(
            "S",
            (),
            {
                "settimeout": lambda self, t: None,
                "connect": lambda self, p: captured.append("uds"),
                "sendall": lambda self, b: captured.append(b.decode()),
                "__enter__": lambda self: self,
                "__exit__": lambda *a: None,
            },
        )(),
    )
    emit(
        mcp_cse_session_pasted(
            registration_id="reg-1",
            receipt="dom_paste",
            send_verified=True,
        )
    )
    assert captured
    assert "ack_class" not in captured[-1]
    assert "mcp.cse.session.pasted" in captured[-1]


def test_harvest_and_ack_events_distinct() -> None:
    harvested = mcp_cse_session_harvested(
        registration_id="reg-1",
        outcome="harvested",
        ack_class="ordinary_content",
        turn_count=1,
        reason="settled_empty",
        waited_ms=500,
    )
    acked = mcp_cse_session_acknowledged(
        registration_id="reg-1",
        ack_class="typed_ack",
    )
    assert harvested.signal == "mcp.cse.session.harvested"
    assert acked.signal == "mcp.cse.session.acknowledged"
    assert harvested.signal != acked.signal
    assert harvested.payload["reason"] == "settled_empty"
    assert harvested.payload["waited_ms"] == 500
