"""Pipeline handler smoke tests (hermetic)."""

from __future__ import annotations

import json

import pytest
from web_tab.cdp_port import RecordingCdpPort

from .probe import WebTabProbeHandler
from .status import WebTabStatusHandler


class _Ctx:
    def __init__(self, options: dict) -> None:
        self.options = options


@pytest.mark.asyncio
async def test_status_handler_ignores_untrusted_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct pipeline options cannot prove connector liveness or principal match."""
    port = RecordingCdpPort(tabs=[], reachable=False, list_error="URLError")
    monkeypatch.setattr("web_tab.status_report.default_cdp_port", lambda: port)
    monkeypatch.setattr(
        "web_tab.status_report.default_attestation_reader",
        lambda house_id, agent: {
            "available": True,
            "turn": {
                "turn_number": 9,
                "attested_by": "oauth:orion@code",
                "created_at": "2026-10-31T00:00:00Z",
            },
        },
    )
    monkeypatch.setattr(
        "web_tab.status_report._resolve_binding",
        lambda house_id, agent: None,
    )
    handler = WebTabStatusHandler()
    out = await handler.execute(
        None,
        _Ctx(
            {
                "house_id": "21207",
                "agent": "orion",
                "cdp_url": "http://127.0.0.1:9342",
                "caller_identity": "oauth:spoof@code",
                "oauth_client_id": "oauth:spoof@code",
                "authenticated_principal": "oauth:spoof@code",
            }
        ),
    )
    data = json.loads(out.raw)
    assert data["ok"] is True
    assert data["connector"]["connector_alive"]["connector_alive"] is None
    assert data["connector"]["principal_matches_attested_turn"] is None
    assert data["connector"]["request_identity"] is None
    assert data["connector"]["last_attested_turn"]["last_attested_turn"] == 9


@pytest.mark.asyncio
async def test_status_handler_list_mode_without_house(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "web_tab.status_report.enumerate_house_agent_tab_index_pairs",
        lambda: [],
    )
    handler = WebTabStatusHandler()
    out = await handler.execute(None, _Ctx({}))
    data = json.loads(out.raw)
    assert data["ok"] is True
    assert data["mode"] == "list"
    assert data["rows"] == []


@pytest.mark.asyncio
async def test_status_handler_single_house_still_requires_agent() -> None:
    handler = WebTabStatusHandler()
    out = await handler.execute(None, _Ctx({"house_id": "21207"}))
    data = json.loads(out.raw)
    assert data["ok"] is False


@pytest.mark.asyncio
async def test_probe_handler_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    port = RecordingCdpPort(
        tabs=[{"id": "x", "url": "https://chatgpt.com/c/x", "title": ""}],
        evaluate_results={"x": {"signed_in": True}},
    )
    monkeypatch.setattr(
        "web_tab.probe.default_cdp_port",
        lambda: port,
    )
    handler = WebTabProbeHandler()
    out = await handler.execute(
        None,
        _Ctx({"target_id": "x", "cdp_url": "http://127.0.0.1:9342"}),
    )
    data = json.loads(out.raw)
    assert data["ok"] is True
    assert data["read_only"] is True
