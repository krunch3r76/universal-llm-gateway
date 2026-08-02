"""Hermetic tests for CDP orphan observation plane."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles import cdp_orphans
from claude_bundles.cdp_orphans import LivePort, Orphan, OrphanScanResult, RejectedPort

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


def _empty_scan() -> OrphanScanResult:
    return OrphanScanResult(matched=(), rejected=(), unevaluable=())


def _patch_proc_cmdline(
    monkeypatch: pytest.MonkeyPatch, pid: int, cmdline: bytes
) -> None:
    class _CmdlineFile:
        def read_bytes(self) -> bytes:
            return cmdline

    def _path_factory(path: str) -> _CmdlineFile | Path:
        if path == f"/proc/{pid}/cmdline":
            return _CmdlineFile()
        return Path(path)

    monkeypatch.setattr(cdp_orphans, "Path", _path_factory)


@pytest.mark.parametrize(
    ("cmdline", "expected"),
    [
        (
            b"/opt/chrome\x00--user-data-dir=/home/u/.gateway/claude-ai-chrome-profile-reg-abc\x00",
            Path("/home/u/.gateway/claude-ai-chrome-profile-reg-abc"),
        ),
        (
            b"/opt/chrome\x00--user-data-dir\x00/home/u/.gateway/claude-ai-chrome-profile-reg-abc\x00",
            Path("/home/u/.gateway/claude-ai-chrome-profile-reg-abc"),
        ),
    ],
)
def test_profile_from_pid_both_user_data_dir_forms(
    monkeypatch: pytest.MonkeyPatch,
    cmdline: bytes,
    expected: Path,
) -> None:
    _patch_proc_cmdline(monkeypatch, 4242, cmdline)
    assert cdp_orphans._profile_from_pid(4242) == expected


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

    scan = cdp_orphans.find_orphans()
    assert scan.rejected == ()
    assert scan.unevaluable == ()
    assert len(scan.matched) == 1
    assert scan.matched[0] == Orphan(
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

    scan = cdp_orphans.find_orphans()
    assert scan.matched == ()
    assert scan.unevaluable == ()
    assert len(scan.rejected) == 1
    assert scan.rejected[0].reason == "primary_profile"


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

    scan = cdp_orphans.find_orphans()
    assert scan.matched == ()
    assert scan.unevaluable == ()
    assert len(scan.rejected) == 1
    assert scan.rejected[0].reason == "non_reg_profile"


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

    scan = cdp_orphans.find_orphans()
    assert scan == _empty_scan()


def test_find_orphans_surfaces_unresolvable_profile_as_unevaluable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cdp_orphans,
        "probe_live_ports",
        lambda port_range=None: [_live(9224, None, has_live_cse=True)],
    )
    monkeypatch.setattr(cdp_orphans, "_pid_listening_on", lambda _p: 2544134)
    monkeypatch.setattr(cdp_orphans, "_registered_ports", lambda: set())

    scan = cdp_orphans.find_orphans()
    assert scan.matched == ()
    assert scan.rejected == ()
    assert len(scan.unevaluable) == 1
    assert scan.unevaluable[0].port == 9224
    assert scan.unevaluable[0].reason == "profile_unresolved"
    assert scan.unevaluable[0].has_live_cse is True


def test_probe_live_ports_degrades_on_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cdp_orphans, "_fetch_json", lambda _url: None)
    assert cdp_orphans.probe_live_ports(port_range=range(9223, 9225)) == []
