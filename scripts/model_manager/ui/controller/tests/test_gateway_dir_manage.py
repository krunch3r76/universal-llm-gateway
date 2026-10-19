"""Manage P1 gateway dir resolution under dispatch-style HOME."""

from __future__ import annotations

import importlib
import os
import pwd
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.offline

_MANAGE_RELOAD: tuple[str, ...] = (
    "scripts.model_manager.ui.controller.service_config",
    "scripts.model_manager.ui.model.service_state",
    "scripts.model_manager.topology",
    "scripts.model_manager.ui.controller.topology",
    "scripts.model_manager.relay",
    "scripts.model_manager.relay_state",
    "scripts.model_manager.update_targets",
    "scripts.model_manager.guarded_manage_reexec.checks",
)


@pytest.fixture
def dispatch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    real_pw_dir = pwd.getpwuid(os.getuid()).pw_dir

    dispatch = tmp_path / "cursor-dispatch-homes" / "x-home"
    dispatch.mkdir(parents=True)
    dispatch_gw = dispatch / ".gateway"
    dispatch_gw.mkdir()
    (dispatch_gw / "mcp.yaml").write_text("{}", encoding="utf-8")

    operator = tmp_path / "operator"
    operator.mkdir()
    operator_gw = operator / ".gateway"
    operator_gw.mkdir()

    monkeypatch.setenv("HOME", str(dispatch))
    monkeypatch.delenv("GATEWAY_DIR", raising=False)

    class _FakePasswd:
        pw_dir = str(operator)

    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: _FakePasswd())

    return {
        "dispatch_home": dispatch,
        "dispatch_gateway": dispatch_gw,
        "operator_gateway": operator_gw,
        "real_pw_dir": real_pw_dir,
        "tmp_path": tmp_path,
    }


def _reload_manage_modules() -> dict[str, ModuleType]:
    loaded: dict[str, ModuleType] = {}
    for name in _MANAGE_RELOAD:
        if name in sys.modules:
            loaded[name] = importlib.reload(sys.modules[name])
        else:
            loaded[name] = importlib.import_module(name)
    return loaded


def test_service_config_gateway_dir_ignores_env(
    dispatch_home: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("GATEWAY_DIR", str(tmp_path / "wrong"))
    mods = _reload_manage_modules()
    service_config = mods["scripts.model_manager.ui.controller.service_config"]
    assert service_config.GATEWAY_DIR == dispatch_home["operator_gateway"]
    assert str(service_config.GATEWAY_DIR).startswith(str(dispatch_home["tmp_path"]))


def test_service_state_pid_files_and_cloud_proxy_config_path(
    dispatch_home: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[Path] = []

    def _fake_load_config(path: Path) -> object:
        recorded.append(path)
        return type("Cfg", (), {"socket_path": "/tmp/x.sock", "host": "127.0.0.1", "port": 1})()

    monkeypatch.setattr(
        "services.universal_cloud_proxy.config.load_config",
        _fake_load_config,
    )
    mods = _reload_manage_modules()
    service_config = mods["scripts.model_manager.ui.controller.service_config"]
    service_state_mod = mods["scripts.model_manager.ui.model.service_state"]
    svc_state = service_state_mod.ServiceState

    gw = service_config.GATEWAY_DIR
    assert svc_state.STARGATE_PID_FILE == gw / "stargate.pid"
    assert svc_state.RAG_PID_FILE == gw / "rag.pid"
    assert svc_state.CLOUD_PROXY_PID_FILE == gw / "cloud-proxy.pid"
    assert svc_state.CORTEX_API_PID_FILE == gw / "cortex-api.pid"
    assert svc_state.AGENT_BUS_PID_FILE == gw / "agent-bus.pid"
    assert svc_state.EMAIL_BRIDGE_PID_FILE == gw / "email-bridge.pid"
    assert svc_state.JOBS_PID_FILE == gw / "jobs.pid"
    assert svc_state.GIT_INTEGRATION_WORKER_PID_FILE == gw / "git-integration-worker.pid"
    assert svc_state.CDP_ASK_PID_FILE == gw / "cdp-ask.pid"
    assert svc_state.EVENT_SERVICE_PID_FILE == gw / "event-service.pid"

    assert service_config._RAG_CONFIG_PATH == gw / "rag.yaml"

    state = svc_state(Path("/tmp/ws"))
    state.check_cloud_proxy()
    assert recorded
    assert all(path == gw / "cloud-proxy.yaml" for path in recorded)


def test_topology_and_relay_gateway_dirs_match_service_config(
    dispatch_home: dict[str, Any],
) -> None:
    mods = _reload_manage_modules()
    service_config = mods["scripts.model_manager.ui.controller.service_config"]
    topology = mods["scripts.model_manager.topology"]
    ui_topology = mods["scripts.model_manager.ui.controller.topology"]
    relay = mods["scripts.model_manager.relay"]
    relay_state = mods["scripts.model_manager.relay_state"]
    update_targets = mods["scripts.model_manager.update_targets"]

    gw = service_config.GATEWAY_DIR
    for mod in (topology, ui_topology, relay, relay_state, update_targets):
        assert mod._GATEWAY_DIR == gw
    assert topology.TOPOLOGY_FILE == gw / "topology.yaml"


def test_write_cloud_proxy_url_to_stargate_uses_passwd_gateway(
    dispatch_home: dict[str, Any],
) -> None:
    mods = _reload_manage_modules()
    service_config = mods["scripts.model_manager.ui.controller.service_config"]
    gw = service_config.GATEWAY_DIR
    assert str(gw).startswith(str(dispatch_home["tmp_path"]))

    service_config.write_cloud_proxy_url_to_stargate("http://127.0.0.1:9999/v1")
    stargate = dispatch_home["operator_gateway"] / "stargate.yaml"
    assert stargate.is_file()
    assert not (dispatch_home["dispatch_gateway"] / "stargate.yaml").exists()


def test_checks_resolve_gateway_dir_last_resort_passwd(
    dispatch_home: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GATEWAY_DIR", raising=False)
    mods = _reload_manage_modules()
    checks = mods["scripts.model_manager.guarded_manage_reexec.checks"]
    assert checks.resolve_gateway_dir(manage_pid=None) == dispatch_home["operator_gateway"]
