"""AC1: connector and actuation planes report independently."""

from __future__ import annotations

import asyncio

import pytest

from web_tab.cdp_port import RecordingCdpPort
from web_tab.connector_plane import observe_connector_plane_from_server
from web_tab.observation import observe_actuation_plane
from web_tab.status_report import build_status_report


@pytest.mark.asyncio
async def test_status_planes_independent() -> None:
    """Connector alive with CDP down yields independent unknown/stale actuation."""
    port = RecordingCdpPort(tabs=[], reachable=False, list_error="URLError")
    connector = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        caller_identity="oauth:test",
        oauth_client_id="oauth:test",
        last_attested_turn=375,
        attestation_source="bus.attestation",
    )
    actuation = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9342",
        target_id="tgt-a",
        port=port,
        probe_js="() => ({ signed_in: false })",
    )
    assert connector["connector_alive"]["connector_alive"] is True
    assert actuation["signed_in"]["signed_in"] is None
    assert actuation["signed_in"].get("stale") is True
    assert actuation["cdp_reachable"]["cdp_reachable"] is False

    report = await build_status_report(
        house_id="21207",
        agent="orion",
        target_id="tgt-a",
        cdp_url="http://127.0.0.1:9342",
        caller_identity="oauth:test",
        oauth_client_id="oauth:test",
        port=port,
    )
    assert report["connector"]["connector_alive"]["connector_alive"] is True
    assert report["actuation"]["signed_in"]["signed_in"] is None


def test_logged_out_actuation_plane() -> None:
    """Actuation signed_in false does not imply connector dead."""
    port = RecordingCdpPort(
        tabs=[{"id": "t1", "url": "https://chatgpt.com/c/abc", "title": "x"}],
        evaluate_results={
            "t1": {"signed_in": False, "login_wall": True, "url": "https://chatgpt.com/"},
        },
    )
    actuation = asyncio.run(
        observe_actuation_plane(
            cdp_url="http://127.0.0.1:9342",
            target_id="t1",
            port=port,
            probe_js="() => ({})",
        )
    )
    assert actuation["signed_in"]["signed_in"] is False
    conn = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        caller_identity="oauth:test",
        oauth_client_id=None,
        last_attested_turn=400,
        attestation_source="bus",
    )
    assert conn["connector_alive"]["connector_alive"] is True


@pytest.mark.asyncio
async def test_connector_plane_ignores_absent_caller_claims() -> None:
    """Without server stamps, connector_alive is false — not caller-supplied booleans."""
    conn = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        caller_identity=None,
        oauth_client_id=None,
        last_attested_turn=None,
        attestation_source=None,
    )
    assert conn["connector_alive"]["connector_alive"] is False
