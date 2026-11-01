"""Connector plane: stored attestation is not current liveness."""

from __future__ import annotations

import os

import pytest

from web_tab.connector_plane import (
    default_attestation_reader,
    observe_connector_plane_from_server,
    principal_matches_attested_turn,
)
from web_tab.status_report import build_status_report


def test_principal_equality_rejects_prefix() -> None:
    """Prefix overlap is not a principal match; missing sides stay unknown."""
    full = "oauth:abc@code via=unknown(1.2.3.4)"
    assert principal_matches_attested_turn("oauth:abc@code", full) is False
    assert principal_matches_attested_turn(full, full) is True
    assert principal_matches_attested_turn(None, full) is None
    assert principal_matches_attested_turn("", full) is None


def test_historical_attestation_leaves_connector_unknown() -> None:
    """A stored attested turn exposes age and does not prove the connector is alive."""
    plane = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        attestation={
            "available": True,
            "turn": {
                "turn_number": 40,
                "attested_by": "oauth:abc@code via=unknown(1.2.3.4)",
                "created_at": "2020-01-01T00:00:00Z",
                "from_agent": "orion",
            },
        },
        authenticated_principal="oauth:abc@code via=unknown(1.2.3.4)",
    )
    assert plane["connector_alive"]["connector_alive"] is None
    assert plane["connector_alive"]["source"] == "connector.unobserved"
    assert plane["last_attested_turn"]["last_attested_turn"] == 40
    assert plane["last_attested_turn"]["as_of"] == "2020-01-01T00:00:00Z"
    assert plane["last_attested_turn"]["epoch"]["attestation_age_s"] > 0
    assert plane["principal_matches_attested_turn"] is True
    assert "house_owned" not in plane
    assert "principal_matches_house" not in plane


def test_principal_mismatch_reported() -> None:
    """Exact principal mismatch is false and still does not claim connector liveness."""
    plane = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        attestation={
            "available": True,
            "turn": {
                "turn_number": 3,
                "attested_by": "oauth:orion@code",
                "created_at": "2026-10-31T00:00:00Z",
            },
        },
        authenticated_principal="oauth:other@code",
    )
    assert plane["principal_matches_attested_turn"] is False
    assert plane["connector_alive"]["connector_alive"] is None


def test_bus_unavailable_is_unknown_not_crash() -> None:
    """An unavailable bus read stays unknown instead of raising or claiming death."""
    plane = observe_connector_plane_from_server(
        house_id="21207",
        agent="orion",
        attestation={"available": False, "turn": None, "reason": "bus_unavailable"},
        authenticated_principal="oauth:orion@code",
    )
    assert plane["connector_alive"]["connector_alive"] is None
    assert plane["last_attested_turn"]["last_attested_turn"] is None
    assert plane["last_attested_turn"]["source"] == "bus.unavailable"
    assert plane["principal_matches_attested_turn"] is None


def test_missing_db_leaves_filesystem_unchanged(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing bus database path is unavailable and is not created on disk."""
    missing = tmp_path / "absent" / "messages.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(missing))
    before = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    read = default_attestation_reader("21207", "orion")
    after = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*"))
    assert read["available"] is False
    assert read["reason"] == "missing_db"
    assert after == before
    assert not missing.exists()
    assert os.environ["AGENT_BUS_DB_PATH"] == str(missing)


@pytest.mark.asyncio
async def test_status_report_missing_db_does_not_create(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "no-bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(missing))
    from web_tab.cdp_port import RecordingCdpPort

    port = RecordingCdpPort(tabs=[], reachable=False, list_error="URLError")
    report = await build_status_report(
        house_id="21207",
        agent="orion",
        cdp_url="http://127.0.0.1:9342",
        port=port,
    )
    assert not missing.exists()
    assert report["connector"]["connector_alive"]["connector_alive"] is None
    assert report["connector"]["last_attested_turn"]["source"] == "bus.unavailable"
