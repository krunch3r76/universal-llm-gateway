"""Dispatch-style HOME tests for gateway_dir and P1 libs call sites."""

from __future__ import annotations

import importlib
import os
import pwd
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

from transport_utils.client_factory import DEFAULT_RAG_URL
from transport_utils.gateway_dir import (
    passwd_home,
    resolve_gateway_data_dir,
    resolve_gateway_dir,
)

pytestmark = pytest.mark.offline

_LIBS_RELOAD: tuple[str, ...] = (
    "transport_utils.rag_client",
    "llm_adapters._mcp_entry",
    "durable_sink",
    "event_store.dispatch_journal",
    "event_store.delivery_audit_schema",
    "event_store.model_rate_table",
    "research_dialogue.driver",
)


@pytest.fixture
def dispatch_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict[str, Any]:
    """Dispatch HOME with only mcp.yaml; passwd home is a separate fake operator dir."""
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
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.delenv("MCP_CONFIG_PATH", raising=False)
    monkeypatch.delenv("MODEL_RATES_CATALOG_PATH", raising=False)
    monkeypatch.delenv("RESEARCH_DIALOGUE_GATEWAY_DIR", raising=False)

    class _FakePasswd:
        pw_dir = str(operator)

    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: _FakePasswd())

    return {
        "dispatch_home": dispatch,
        "dispatch_gateway": dispatch_gw,
        "operator_home": operator,
        "operator_gateway": operator_gw,
        "real_pw_dir": real_pw_dir,
    }


def _reload_modules(names: tuple[str, ...]) -> dict[str, ModuleType]:
    loaded: dict[str, ModuleType] = {}
    for name in names:
        if name in sys.modules:
            loaded[name] = importlib.reload(sys.modules[name])
        else:
            loaded[name] = importlib.import_module(name)
    return loaded


def _assert_not_real_home(path: Path, real_pw_dir: str) -> None:
    resolved = str(path.resolve())
    assert not resolved.startswith(real_pw_dir), resolved


def test_resolve_gateway_dir_passwd_not_dispatch_home(
    dispatch_home: dict[str, Any],
) -> None:
    resolved = resolve_gateway_dir()
    assert resolved == dispatch_home["operator_gateway"]
    assert resolved != dispatch_home["dispatch_gateway"]
    _assert_not_real_home(resolved, dispatch_home["real_pw_dir"])


def test_resolve_gateway_dir_precedence_and_env_var_none(
    dispatch_home: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_gw = tmp_path / "from-env"
    monkeypatch.setenv("GATEWAY_DIR", str(env_gw))
    explicit = tmp_path / "explicit"
    assert resolve_gateway_dir(explicit) == explicit
    assert resolve_gateway_dir() == env_gw
    assert resolve_gateway_dir(env_var=None) == dispatch_home["operator_gateway"]


def test_resolve_gateway_data_dir_data_dir_wins(
    dispatch_home: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    data = tmp_path / "data-root"
    monkeypatch.setenv("DATA_DIR", str(data))
    assert resolve_gateway_data_dir() == data
    monkeypatch.delenv("DATA_DIR", raising=False)
    assert resolve_gateway_data_dir() == dispatch_home["operator_gateway"]


def test_passwd_home_keyerror_strict_and_fallback(
    monkeypatch: pytest.MonkeyPatch, dispatch_home: dict[str, Any]
) -> None:
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: (_ for _ in ()).throw(KeyError))
    with pytest.raises(KeyError):
        passwd_home(strict=True)
    assert passwd_home(strict=False) == dispatch_home["dispatch_home"]


def test_resolve_gateway_dir_happy_path_never_calls_path_home(
    monkeypatch: pytest.MonkeyPatch, dispatch_home: dict[str, Any]
) -> None:
    def _boom() -> Path:
        raise AssertionError("Path.home must not run on passwd happy path")

    monkeypatch.setattr(Path, "home", staticmethod(_boom))
    assert resolve_gateway_dir() == dispatch_home["operator_gateway"]


def test_rag_client_uses_passwd_stargate_yaml(
    dispatch_home: dict[str, Any],
) -> None:
    stargate = dispatch_home["operator_gateway"] / "stargate.yaml"
    stargate.write_text(
        yaml.safe_dump({"rag": {"host": "127.0.0.1", "port": 8199}}),
        encoding="utf-8",
    )
    mods = _reload_modules(("transport_utils.rag_client",))
    rag_client = mods["transport_utils.rag_client"]
    url = rag_client.resolve_rag_base_url()
    assert url == "http://127.0.0.1:8199"
    assert url != DEFAULT_RAG_URL
    _assert_not_real_home(rag_client._stargate_config_path(), dispatch_home["real_pw_dir"])


def test_p1_libs_defaults_under_passwd_gateway(
    dispatch_home: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    mods = _reload_modules(_LIBS_RELOAD)
    mcp_entry = mods["llm_adapters._mcp_entry"]
    durable_sink = mods["durable_sink"]
    mrt = mods["event_store.model_rate_table"]
    dispatch_journal = mods["event_store.dispatch_journal"]
    delivery_audit = mods["event_store.delivery_audit_schema"]

    assert mcp_entry._mcp_config_path() == dispatch_home["operator_gateway"] / "mcp.yaml"
    assert durable_sink._STARGATE_CONFIG_PATH == (
        dispatch_home["operator_gateway"] / "stargate.yaml"
    )
    assert mrt._CATALOG_PATH == dispatch_home["operator_gateway"] / "model_rates_catalog.yaml"
    assert dispatch_journal.dispatch_journal_path().parent == dispatch_home["operator_gateway"]
    assert delivery_audit.delivery_audit_db_path().parent == dispatch_home["operator_gateway"]

    mcp_override = tmp_path / "custom-mcp.yaml"
    monkeypatch.setenv("MCP_CONFIG_PATH", str(mcp_override))
    assert mcp_entry._mcp_config_path() == mcp_override

    data_override = tmp_path / "custom-data"
    monkeypatch.setenv("DATA_DIR", str(data_override))
    assert dispatch_journal.dispatch_journal_path().parent == data_override

    catalog_override = tmp_path / "rates.yaml"
    monkeypatch.setenv("MODEL_RATES_CATALOG_PATH", str(catalog_override))
    assert mrt.catalog_yaml_path() == catalog_override


def test_model_rate_table_catalog_path_monkeypatch_still_works(
    dispatch_home: dict[str, Any], tmp_path: Path
) -> None:
    mods = _reload_modules(("event_store.model_rate_table",))
    mrt = mods["event_store.model_rate_table"]
    custom = tmp_path / "assigned-catalog.yaml"
    mrt._CATALOG_PATH = custom
    assert mrt.catalog_yaml_path() == custom


def test_research_dialogue_resolve_gateway_dir_strict_keyerror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: (_ for _ in ()).throw(KeyError))
    driver = importlib.import_module("research_dialogue.driver")
    driver = importlib.reload(driver)
    with pytest.raises(KeyError):
        driver.resolve_gateway_dir()
