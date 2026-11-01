"""AC1: connector and actuation planes report independently."""

from __future__ import annotations

import asyncio

import pytest

from web_tab.cdp_port import RecordingCdpPort
from web_tab.connector_plane import observe_connector_plane_from_server
from web_tab.observation import observe_actuation_plane
from web_tab.status_report import build_status_report


def _attestation(turn_no: int) -> dict:
    return {
        "available": True,
        "turn": {
            "turn_number": turn_no,
            "attested_by": "oauth:test@code",
            "created_at": "2026-10-31T00:00:00Z",
        },
    }


@pytest.mark.asyncio
async def test_status_planes_independent() -> None:
    """Stored attestation does not make connector_alive true while CDP is down."""
    port = RecordingCdpPort(tabs=[], reachable=False, list_error="URLError")
    connector = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        attestation=_attestation(375),
    )
    actuation = await observe_actuation_plane(
        cdp_url="http://127.0.0.1:9342",
        target_id="tgt-a",
        port=port,
        probe_js="() => ({ signed_in: false })",
    )
    assert connector["connector_alive"]["connector_alive"] is None
    assert connector["last_attested_turn"]["last_attested_turn"] == 375
    assert actuation["signed_in"]["signed_in"] is None
    assert actuation["signed_in"].get("stale") is True
    assert actuation["cdp_reachable"]["cdp_reachable"] is False

    report = await build_status_report(
        house_id="21207",
        agent="orion",
        target_id="tgt-a",
        cdp_url="http://127.0.0.1:9342",
        port=port,
        attestation_reader=lambda house_id, agent: _attestation(375),  # noqa: ARG005
        binding_resolver=lambda house_id, agent: None,  # noqa: ARG005
    )
    assert report["connector"]["connector_alive"]["connector_alive"] is None
    assert report["actuation"]["signed_in"]["signed_in"] is None


def test_logged_out_actuation_plane() -> None:
    """Actuation signed_in false does not imply connector dead."""
    port = RecordingCdpPort(
        tabs=[{"id": "t1", "url": "https://chatgpt.com/c/abc", "title": "x"}],
        evaluate_results={
            "t1": {
                "signed_in": False,
                "login_wall": True,
                "url": "https://chatgpt.com/",
            },
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
        attestation=_attestation(400),
    )
    assert conn["connector_alive"]["connector_alive"] is None


@pytest.mark.asyncio
async def test_connector_plane_ignores_absent_caller_claims() -> None:
    """No stored attestation leaves connector_alive unknown, not a caller boolean."""
    conn = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        attestation={"available": True, "turn": None},
    )
    assert conn["connector_alive"]["connector_alive"] is None
    assert conn["principal_matches_attested_turn"] is None
