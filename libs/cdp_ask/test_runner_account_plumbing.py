"""Runner account_id plumbing into tab pool (offline)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from claude_bundles.cdp_registry.models import RegistryError
from claude_bundles.cdp_registry.tab_rows import eligible_host, open_tab_row_for_ask

from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import _open_tab_lane

pytestmark = pytest.mark.offline


def test_eligible_host_default_ask_only_account_two_host() -> None:
    active: dict[str, Any] = {
        "host-a2": {
            "registration_id": "host-a2",
            "status": "active",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9226,
            "display": ":2",
            "account_id": "account-2",
        },
    }

    def listen(p: int) -> bool:
        return p == 9226

    assert eligible_host(active, [":2"], listen=listen, account_id=None) is None


def test_open_tab_lane_passes_account_id_and_isolates_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    active: dict[str, Any] = {
        "host-default": {
            "registration_id": "host-default",
            "status": "active",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9225,
            "display": ":2",
            "account_id": "default",
        },
        "host-a2": {
            "registration_id": "host-a2",
            "status": "active",
            "purpose": "tab_host",
            "holder": "cdp-tab-pool",
            "port": 9226,
            "display": ":2",
            "account_id": "account-2",
        },
    }

    def listen(p: int) -> bool:
        return p in {9225, 9226}

    assert (
        eligible_host(active, [":2"], listen=listen, account_id=None)
        is active["host-default"]
    )
    assert (
        eligible_host(active, [":2"], listen=listen, account_id="account-2")
        is active["host-a2"]
    )

    signin = tmp_path / "account_sign_in.json"
    signin.write_text(
        json.dumps({"accounts": {"account-2": {"signed_in": True}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CDP_ACCOUNT_SIGNIN_PATH", str(signin))

    open_calls: list[dict[str, Any]] = []

    def _fake_open_tab_row_for_ask(**kwargs):
        open_calls.append(kwargs)
        from claude_bundles.cdp_registry.models import Registration

        return Registration(
            registration_id="tab-1",
            port=9227,
            profile_suffix="account-2",
            profile=Path("/tmp/p"),
            cdp_url="http://127.0.0.1:9227",
            holder="holder-x",
            purpose="ask",
            account_id="account-2",
        )

    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows.open_tab_row_for_ask",
        _fake_open_tab_row_for_ask,
    )

    req = SubmitProjectAskRequest(
        prompt_text="hi",
        account_id="account-2",
        purpose="ask",
    )
    _open_tab_lane(req, holder="holder-x")
    assert open_calls
    assert open_calls[-1]["account_id"] == "account-2"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    from claude_bundles import cdp_registry as reg

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def test_open_tab_row_register_lane_receives_account_id(
    isolated_registry: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claude_bundles.cdp_registry import lifecycle

    signin = isolated_registry / "account_sign_in.json"
    signin.write_text(
        json.dumps({"accounts": {"account-2": {"signed_in": True}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CDP_ACCOUNT_SIGNIN_PATH", str(signin))

    register_calls: list[dict[str, Any]] = []
    real_register = lifecycle.register_lane

    def _capture_register_lane(**kwargs):
        register_calls.append(dict(kwargs))
        return real_register(**{**kwargs, "launch": False, "is_listening": lambda _p: False})

    monkeypatch.setattr(lifecycle, "register_lane", _capture_register_lane)
    monkeypatch.setattr(
        "claude_bundles.cdp_lane.open_cdp_tab_target",
        lambda _port, _url: "target-1",
    )

    open_tab_row_for_ask(
        holder="holder-x",
        purpose="ask",
        mission_kind=None,
        parent_thread=None,
        account_id="account-2",
    )
    assert register_calls
    assert register_calls[-1]["account_id"] == "account-2"


def test_open_tab_row_host_row_account_mismatch() -> None:
    with pytest.raises(RegistryError, match="host_row account"):
        open_tab_row_for_ask(
            holder="h",
            purpose="ask",
            mission_kind=None,
            parent_thread=None,
            account_id="account-2",
            host_row={
                "registration_id": "x",
                "status": "active",
                "purpose": "tab_host",
                "port": 9225,
                "display": ":2",
                "account_id": "default",
            },
        )


def test_open_tab_lane_refuses_unsigned_account(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    signin = tmp_path / "account_sign_in.json"
    signin.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CDP_ACCOUNT_SIGNIN_PATH", str(signin))

    req = SubmitProjectAskRequest(
        prompt_text="hi",
        account_id="account-2",
        purpose="ask",
    )
    with pytest.raises(RuntimeError, match="account routing refused"):
        _open_tab_lane(req, holder="holder-x")
