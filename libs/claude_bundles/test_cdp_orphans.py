"""Hermetic tests for CDP orphan observation plane."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles import cdp_orphans
from claude_bundles.cdp_orphans import LivePort, Orphan

pytestmark = pytest.mark.offline


def _live(
    port: int,
    profile: Path | None,
    *,
    has_live_cse: bool = False,
    urls: tuple[str, ...] = (),
) -> LivePort:
    return LivePort(
        port=port,
        profile=profile,
        page_urls=urls,
        has_live_cse=has_live_cse,
    )


def test_find_orphans_includes_unregistered_reg_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg_profile = tmp_path / "claude-ai-chrome-profile-reg-deadbeef"
    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [_live(9229, reg_profile, has_live_cse=True)],
    )
    monkeypatch.setattr(cdp_orphans, "_pid_listening_on", lambda _p: 4242)
    monkeypatch.setattr(cdp_orphans, "_process_uptime_s", lambda _p: 12.5)
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())

    orphans = cdp_orphans.find_orphans()
    assert len(orphans) == 1
    assert orphans[0] == Orphan(
        port=9229,
        pid=4242,
        profile=reg_profile,
        has_live_cse=True,
        uptime_s=12.5,
    )


def test_find_orphans_excludes_primary_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primary = tmp_path / "claude-ai-chrome-profile"
    monkeypatch.setattr(cdp_orphans.cdp_registry.cdp_lane, "PRIMARY_PROFILE", primary)
    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [_live(9222, primary)],
    )
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())

    assert cdp_orphans.find_orphans() == []


def test_find_orphans_excludes_non_reg_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bridge = tmp_path / "claude-ai-chrome-profile-messages-bridge"
    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [_live(9250, bridge, has_live_cse=True)],
    )
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())

    assert cdp_orphans.find_orphans() == []


def test_find_orphans_excludes_registered_port(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg_profile = tmp_path / "claude-ai-chrome-profile-reg-live01"
    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [_live(9229, reg_profile)],
    )
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: {9229})

    assert cdp_orphans.find_orphans() == []


def test_probe_live_ports_degrades_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cdp_orphans, "_fetch_json", lambda _url: None)
    assert cdp_orphans.probe_live_ports(port_range=range(9223, 9225)) == []
