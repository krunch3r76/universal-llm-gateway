"""Account-keyed CDP profile seed and registry suffix tests (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from claude_bundles import cdp_lane
from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_cookie_seed import CookieSeedError, seed_lane_cookies
from claude_bundles.cdp_registry.hygiene import reclaim_profile_for_detached_row

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def test_seed_profile_non_default_account_skips_primary_rsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "claude-ai-chrome-profile-account-2"
    runs: list[list[str]] = []

    def _record_run(argv, **kwargs):
        runs.append(list(argv))
        return mock.Mock(returncode=0)

    monkeypatch.setattr(cdp_lane.subprocess, "run", _record_run)
    cdp_lane._seed_profile(profile, account_id="account-2")
    assert profile.is_dir()
    assert runs == []

    default_profile = tmp_path / "default-profile"
    monkeypatch.setattr(cdp_lane, "PRIMARY_PROFILE", tmp_path / "primary")
    cdp_lane.PRIMARY_PROFILE.mkdir()
    monkeypatch.setattr(
        cdp_lane,
        "seed_profile_rsync_argv",
        lambda src, dest: ["rsync", str(src), str(dest)],
    )
    cdp_lane._seed_profile(default_profile, account_id="default")
    assert len(runs) == 1
    assert runs[0][0] == "rsync"


def test_seed_lane_session_non_default_skips_primary_cookies_and_keeps_chrome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cdp_calls: list[int] = []
    killed: list[int] = []

    def _cdp_call(port: int, method: str, params=None):
        cdp_calls.append(port)
        if port == 9222:
            raise CookieSeedError("primary down")
        return {}

    monkeypatch.setattr("claude_bundles.cdp_cookie_seed._cdp_call", _cdp_call)
    monkeypatch.setattr(cdp_lane, "_kill_lane_chrome", lambda pid: killed.append(pid))

    assert seed_lane_cookies(9225, account_id="account-2") == 0
    assert cdp_calls == []
    cdp_lane._seed_lane_session(9225, 999, account_id="account-2")
    assert cdp_calls == []
    assert killed == []

    monkeypatch.setattr(
        "claude_bundles.cdp_cookie_seed.read_session_cookies",
        lambda *a, **k: (_ for _ in ()).throw(CookieSeedError("signed out")),
    )
    with pytest.raises(CookieSeedError):
        cdp_lane._seed_lane_session(9225, 1001, account_id="default")
    assert killed == [1001]


def test_register_lane_non_default_account_uses_persistent_suffix_and_retains_on_reclaim(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    profiles = tmp_path / "profiles"
    profile_path = profiles / "claude-ai-chrome-profile-account-2"
    profile_path.mkdir(parents=True)

    r = reg.register_lane(
        holder="cdp-tab-pool",
        purpose="tab_host",
        account_id="account-2",
        launch=False,
        is_listening=lambda _p: False,
    )
    assert r.profile_suffix == "account-2"
    assert r.account_id == "account-2"
    active = json.loads((isolated_registry / "active.json").read_text(encoding="utf-8"))
    row = active[r.registration_id]
    assert row["profile_suffix"] == "account-2"
    assert row["account_id"] == "account-2"

    outcome = reclaim_profile_for_detached_row(
        r.registration_id,
        row,
        chrome_port_for_profile=lambda _p: None,
    )
    assert outcome == "retained_account_profile"
    assert profile_path.is_dir()


def test_what_is_running_surfaces_account_id() -> None:
    from claude_bundles import what_is_running_view as wir

    registry = {
        "h1": {
            "registration_id": "h1",
            "status": "active",
            "account_id": "account-2",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9225,
            "started_at": 1.0,
        },
        "h2": {
            "registration_id": "h2",
            "status": "active",
            "account_id": "default",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9226,
            "started_at": 2.0,
        },
    }
    active_work = {
        "rows": [
            {
                "registration_id": "h1",
                "execution_id": "ex-1",
                "purpose": "ask",
                "holder": "cdp-ask",
                "status": "running",
            }
        ],
        "running_count": 1,
    }
    view = wir.compose_view(
        active_work=active_work,
        registry=registry,
        sessions={},
        sources={"registry": "test"},
    )
    assert view["running"][0]["account_id"] == "account-2"
    assert view["registry_account_host_counts"] == {"account-2": 1, "default": 1}
    text = wir.render_text(view)
    assert "account=account-2" in text
