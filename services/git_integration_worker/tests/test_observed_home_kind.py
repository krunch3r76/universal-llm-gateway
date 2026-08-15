"""A2 — observed_home_kind on token and registry reads."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_bundles.cdp_registry_store import (
    classify_observed_home_kind,
    load_active_read,
    load_sessions_read,
)

from services.git_integration_worker.cursor_home import (
    is_dispatch_home_path,
    observed_home_kind,
)
from services.git_integration_worker.cursor_sdk_context import (
    CursorSdkParityError,
    resolve_mcp_token,
    validate_dispatch_context,
)


def _stub_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "universal-llm-gateway"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "mcp-fastmcp-remote-bridge.py").write_text("# stub\n", encoding="utf-8")
    return repo


@pytest.fixture(autouse=True)
def _fastmcp_remote_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.git_integration_worker.cursor_sdk_context.shutil.which",
        lambda cmd: "/usr/bin/fastmcp-remote" if cmd == "fastmcp-remote" else None,
    )


def test_observed_home_kind_dispatch_vs_operator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    leaked = dispatch_root / "auto-deadbeef-home"
    leaked.mkdir(parents=True)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)

    assert observed_home_kind(leaked, root=dispatch_root) == "dispatch"
    assert is_dispatch_home_path(leaked, root=dispatch_root)
    assert observed_home_kind(tmp_path / "operator-home") == "operator"


def test_resolve_mcp_token_miss_names_dispatch_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    leaked = dispatch_root / "auto-deadbeef-home"
    leaked.mkdir(parents=True)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)

    token, source = resolve_mcp_token(real_home=leaked)
    assert token == ""
    assert source == "miss:observed_home_kind=dispatch"


def test_validate_dispatch_context_dispatch_miss_is_scoped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dispatch_root = tmp_path / "cursor-dispatch-homes"
    leaked = dispatch_root / "auto-deadbeef-home"
    leaked.mkdir(parents=True)
    monkeypatch.setenv("CURSOR_DISPATCH_HOME_ROOT", str(dispatch_root))
    import services.git_integration_worker.cursor_home as home_mod

    monkeypatch.setattr(home_mod, "_DISPATCH_HOME_ROOT", dispatch_root)
    monkeypatch.delenv("MCP_TOKEN", raising=False)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    repo = _stub_repo(tmp_path)

    with pytest.raises(CursorSdkParityError, match="observed_home_kind=dispatch"):
        validate_dispatch_context(repo, real_home=leaked)


def test_registry_read_names_dispatch_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "cursor-dispatch-homes" / "auto-probe-home"
    registry = home / ".gateway" / "cdp-registry"
    registry.mkdir(parents=True)
    import claude_bundles.cdp_registry_store as store

    monkeypatch.setattr(store, "REGISTRY_DIR", registry)
    monkeypatch.setattr(store, "ACTIVE_JSON", registry / "active.json")
    monkeypatch.setattr(store, "SESSIONS_JSON", registry / "sessions.json")

    active = load_active_read()
    assert active.data == {}
    assert active.present is False
    assert active.observed_home_kind == "dispatch"
    assert "observed_home_kind=dispatch" in active.miss_label()

    sessions = load_sessions_read()
    assert sessions.data == {}
    assert sessions.observed_home_kind == "dispatch"


def test_classify_observed_home_kind_fingerprint() -> None:
    assert (
        classify_observed_home_kind("/tmp/cursor-dispatch-homes/auto-x-home")
        == "dispatch"
    )
    assert classify_observed_home_kind("/home/io") == "operator"


def test_l2_absent_cse_names_observed_home_kind() -> None:
    from services.git_integration_worker.cursor_auto.l2_orientation import (
        format_cse_state_section,
        read_cse_state,
    )

    cse = read_cse_state(thread_id="nonexistent-thread-xyz")
    assert cse.absent is True
    assert cse.observed_home_kind in {"dispatch", "operator"}
    rendered = format_cse_state_section(cse)
    assert f"observed_home_kind={cse.observed_home_kind}" in rendered
