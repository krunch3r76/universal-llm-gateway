"""cdp_ask routes browser CDP to fleet when ask lane is absent from pins.toml."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles.project_ask import ProjectAskResult
from fastapi.testclient import TestClient

from cdp_ask.app import create_app
from cdp_ask.lane_admission import (
    purpose_lane_refusal,
    resolve_admission_browser_cdp_url,
)
from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.runner import run_execution
from cdp_ask.standing_pins import resolve_cdp_ask_browser_cdp_url

pytestmark = pytest.mark.offline


def _isolate_test_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, pins_toml: str
) -> Path:
    repo = tmp_path / "ulg"
    pins_dir = repo / "services" / "jupiter-cdp"
    pins_dir.mkdir(parents=True)
    (pins_dir / "pins.toml").write_text(pins_toml, encoding="utf-8")

    harvest = tmp_path / "harvest"
    (harvest / "notes" / "system" / "threads").mkdir(parents=True)
    reg_root = tmp_path / "cdp-registry"
    regs = reg_root / "registrations"
    regs.mkdir(parents=True)
    profiles = tmp_path / "profiles"
    profiles.mkdir()

    monkeypatch.setenv("ULG_REPO", str(repo))
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(harvest))
    monkeypatch.setenv("CDP_ASK_TREE_STATE_REFRESH", "0")
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(tmp_path / "ledger"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CDP_REGISTRY_HOME", str(reg_root))
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")

    monkeypatch.setattr(reg._store, "REGISTRY_DIR", reg_root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", reg_root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", reg_root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", reg_root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", reg_root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", reg_root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", reg_root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", reg_root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_registry.tab_rows._TAB_MINT_LOCK",
        tmp_path / "tab_host_mint.lock",
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans.probe_live_ports",
        lambda port_range=None: [],
    )
    return harvest


def test_resolve_cdp_ask_browser_falls_back_to_fleet_when_ask_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Missing ask lane configuration must select fleet :9222, not :9223."""
    repo = tmp_path / "ulg"
    repo.mkdir()
    pins_dir = repo / "services" / "jupiter-cdp"
    pins_dir.mkdir(parents=True)
    (pins_dir / "pins.toml").write_text(
        "[lanes.fleet]\nport = 9222\nstanding = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ULG_REPO", str(repo))
    monkeypatch.setenv("CURSOR_SDK_DISPATCH_LEDGER", str(tmp_path / "ledger"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    url, via = resolve_cdp_ask_browser_cdp_url()
    assert url == "http://127.0.0.1:9222"
    assert via == "fleet"


def test_resolve_cdp_ask_browser_uses_ask_lane_when_configured() -> None:
    lanes = {
        "ask": {"port": 9223},
        "fleet": {"port": 9222},
    }
    url, via = resolve_cdp_ask_browser_cdp_url(lanes=lanes)
    assert url == "http://127.0.0.1:9223"
    assert via == "ask"


@pytest.mark.asyncio
async def test_runner_execution_routes_to_fleet_9222_and_never_dials_9223_when_ask_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runner execution binds standing fleet :9222 when ask lane is absent from pins.toml."""
    _isolate_test_env(
        tmp_path,
        monkeypatch,
        pins_toml='[lanes.fleet]\nport = 9222\ndisplay = ":2"\nstanding = true\n',
    )
    probed_ports: list[int] = []
    tab_ports: list[int] = []
    closed_targets: list[tuple[int, str]] = []
    converse_urls: list[str] = []

    def _is_listening(port: int) -> bool:
        probed_ports.append(port)
        if port == 9223:
            raise AssertionError(
                "cdp_ask must not probe or dial :9223 when ask lane is absent"
            )
        return port == 9222

    def _launch_chrome(port: int, _profile: Path) -> int:
        raise AssertionError(
            f"unexpected Chrome mint on port {port}; expected standing fleet :9222"
        )

    def _open_tab(port: int, _url: str) -> str:
        if port == 9223:
            raise AssertionError("cdp_ask must not open tab on :9223")
        tab_ports.append(port)
        return "tgt-fleet-9222"

    def _close_tab(port: int, target_id: str) -> None:
        closed_targets.append((port, target_id))

    async def _fake_converse(*_args: Any, **kwargs: Any) -> list[ProjectAskResult]:
        converse_urls.append(str(kwargs.get("cdp_url") or ""))
        return [
            ProjectAskResult(
                ok=True,
                body="fleet response",
                url="https://claude.ai/new",
                project_uuid="",
                project_url="https://claude.ai/new",
                model={"requested": "opus-5.5"},
                body_len=14,
                delete_after=False,
                archive_uri="cortex://notes/system/threads/fleet-9222.md",
            )
        ]

    monkeypatch.setattr(reg.cdp_lane, "is_listening", _is_listening)
    monkeypatch.setattr(reg.cdp_lane, "_launch_chrome", _launch_chrome)
    monkeypatch.setattr(reg.cdp_lane, "open_cdp_tab_target", _open_tab)
    monkeypatch.setattr(reg.cdp_lane, "close_cdp_target", _close_tab)

    with (
        patch("cdp_ask.runner.run_project_conversation", side_effect=_fake_converse),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
    ):
        payload = await run_execution(
            SubmitProjectAskRequest(
                prompt_text="smoke check",
                purpose="ask",
                converse=True,
                no_project_uuid=True,
                holder="test-runner",
            ),
            execution_id="exec-fleet-fallback",
            abort_check=AsyncMock(return_value=False),
        )

    assert payload["ok"] is True
    assert tab_ports == [9222]
    assert converse_urls == ["http://127.0.0.1:9222"]
    assert 9223 not in probed_ports
    assert closed_targets == [(9222, "tgt-fleet-9222")]


def test_submit_execution_service_path_routes_to_fleet_9222_when_ask_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Service admission + execution path routes generates to fleet :9222 when ask is absent."""
    harvest = _isolate_test_env(
        tmp_path,
        monkeypatch,
        pins_toml='[lanes.fleet]\nport = 9222\ndisplay = ":2"\nstanding = true\n',
    )
    monkeypatch.setattr("cdp_ask.app.verify_harvest_root", lambda: harvest)

    dialed_urls: list[str] = []
    tab_ports: list[int] = []

    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda port: port == 9222)
    monkeypatch.setattr(
        reg.cdp_lane,
        "_launch_chrome",
        lambda port, _profile: (_ for _ in ()).throw(
            AssertionError(f"unexpected Chrome mint on :{port}")
        ),
    )

    def _open_tab(port: int, _url: str) -> str:
        tab_ports.append(port)
        return "tgt-service-9222"

    monkeypatch.setattr(reg.cdp_lane, "open_cdp_tab_target", _open_tab)
    monkeypatch.setattr(reg.cdp_lane, "close_cdp_target", lambda _p, _t: None)

    async def _fake_converse(*_args: Any, **kwargs: Any) -> list[ProjectAskResult]:
        dialed_urls.append(str(kwargs.get("cdp_url") or ""))
        return [
            ProjectAskResult(
                ok=True,
                body="service fleet ok",
                url="https://claude.ai/new",
                project_uuid="",
                project_url="https://claude.ai/new",
                model={"requested": "opus-5.5"},
                body_len=16,
                delete_after=False,
                archive_uri="cortex://notes/system/threads/service-fleet.md",
            )
        ]

    with (
        patch("cdp_ask.runner.run_project_conversation", side_effect=_fake_converse),
        patch("cdp_ask.runner.registration_has_wake_debt", return_value=False),
    ):
        app = create_app()
        with TestClient(app) as client:
            resp = client.post(
                "/v1/project-ask/executions",
                json={
                    "prompt_text": "service fallback probe",
                    "purpose": "ask",
                    "converse": True,
                    "no_project_uuid": True,
                    "holder": "service-test",
                },
            )
            assert resp.status_code == 202
            exec_id = resp.json()["execution_id"]
            deadline = time.monotonic() + 5.0
            poll_body: dict[str, Any] = {}
            while time.monotonic() < deadline:
                poll_resp = client.get(f"/v1/project-ask/executions/{exec_id}")
                assert poll_resp.status_code == 200
                poll_body = poll_resp.json()
                if poll_body.get("status") != "running":
                    break
                time.sleep(0.05)

    assert poll_body.get("status") == "completed"
    assert poll_body.get("ok") is True
    assert tab_ports == [9222]
    assert dialed_urls == ["http://127.0.0.1:9222"]


def test_lane_admission_resolves_fleet_9222_and_fails_closed_on_down_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Admission selects fleet :9222 when ask is absent; down standing browser fails closed."""
    _isolate_test_env(
        tmp_path,
        monkeypatch,
        pins_toml='[lanes.fleet]\nport = 9222\ndisplay = ":2"\nstanding = true\n',
    )
    assert resolve_admission_browser_cdp_url() == ("http://127.0.0.1:9222", "fleet")
    refuse, label, _env = purpose_lane_refusal(
        {"rows": [], "seat_rows": []},
        purpose="ask",
        unattended=True,
    )
    assert (refuse, label) == (False, None)

    monkeypatch.setattr(reg.cdp_lane, "is_listening", lambda _port: False)
    from claude_bundles.cdp_registry.models import RegistryError

    from cdp_ask.runner import bind_execution_lane

    with pytest.raises(RegistryError, match="not listening on port 9222"):
        bind_execution_lane(
            SubmitProjectAskRequest(prompt_text="x", purpose="ask", holder="h"),
            holder="h",
        )
