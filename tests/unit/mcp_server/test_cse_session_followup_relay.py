"""MCP relay tests for cse_session op=followup / resolve_attended."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_MCP_SERVER = Path(__file__).resolve().parents[3] / "services" / "mcp-server"
if str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))


def _purge_foreign_tools() -> None:
    tools_mod = sys.modules.get("tools")
    tools_file = (getattr(tools_mod, "__file__", None) or "").replace("\\", "/")
    if tools_mod is None or "mcp-server" in tools_file:
        return
    for key in list(sys.modules):
        if key == "tools" or key.startswith("tools."):
            del sys.modules[key]


@pytest.fixture
def cse_session_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite:8770")
    if str(_MCP_SERVER) not in sys.path:
        sys.path.insert(0, str(_MCP_SERVER))
    _purge_foreign_tools()
    import tools.cse_session as session_mod
    import tools.cse_session_warm as warm_mod

    importlib.reload(warm_mod)
    return importlib.reload(session_mod)


def _tool_fn(mod):
    captured: list = []

    class _FakeMcp:
        def tool(self, **_kwargs):
            def _decorator(fn):
                captured.append(fn)
                return fn

            return _decorator

    mod.register_cse_session_tool(_FakeMcp())
    assert captured, "cse_session tool not registered"
    return captured[0]


def test_followup_relay_timeout_uses_caller_budget(
    cse_session_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.cse_session_warm as warm

    relay = MagicMock(
        return_value={
            "ok": True,
            "url": "https://claude.ai/cowork/cse_x",
            "send_verified": True,
        }
    )
    monkeypatch.setattr(warm, "_relay", relay)
    recorded: list[dict] = []
    monkeypatch.setattr(
        warm,
        "record",
        lambda signal, **kwargs: recorded.append({"signal": signal, **kwargs}),
    )
    tool_fn = _tool_fn(cse_session_module)
    result = tool_fn(
        op="followup",
        registration_id="reg-1",
        prompt_text="wake",
        timeout_s=60,
    )
    assert result["ok"] is True
    relay.assert_called_once()
    assert relay.call_args.kwargs["timeout_s"] == 60.0
    assert relay.call_args.args[1] == "/v1/project-ask/followups"
    assert recorded[0]["signal"] == "mcp.cse_session.followup"


def test_followup_no_playwright_imports_in_module(cse_session_module) -> None:
    for path in (
        cse_session_module.__file__,
        (_MCP_SERVER / "tools" / "cse_session_warm.py"),
    ):
        text = Path(path).read_text(encoding="utf-8")
        import_lines = [
            line
            for line in text.splitlines()
            if line.startswith("import ") or line.startswith("from ")
        ]
        joined = "\n".join(import_lines)
        assert "playwright" not in joined
        assert "claude_bundles" not in joined


def test_followup_identity_omitted_relays_to_satellite(
    cse_session_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.cse_session_warm as warm

    relay = MagicMock(return_value={"ok": False, "error": "no_attended_cse"})
    monkeypatch.setattr(warm, "_relay", relay)
    tool_fn = _tool_fn(cse_session_module)
    result = tool_fn(op="followup", prompt_text="x")
    relay.assert_called_once()
    assert result["error"] == "no_attended_cse"


def test_followup_forwards_cdp_url(
    cse_session_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.cse_session_warm as warm

    relay = MagicMock(return_value={"ok": True, "send_verified": True})
    monkeypatch.setattr(warm, "_relay", relay)
    tool_fn = _tool_fn(cse_session_module)
    tool_fn(
        op="followup",
        chat_url="https://claude.ai/cowork/cse_x",
        cdp_url="http://127.0.0.1:9225",
        prompt_text="wake",
    )
    body = relay.call_args.kwargs["json_body"]
    assert body["cdp_url"] == "http://127.0.0.1:9225"


def test_resolve_attended_maps_404(
    cse_session_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.cse_session_warm as warm

    relay = MagicMock(
        return_value={
            "code": "no_attended_cse",
            "message": "No mission-purpose attended CSE registered with bound chat_url",
            "source": "gateway",
            "retryable": True,
            "data": {"candidates_considered": 0, "shadow_urls": []},
        }
    )
    monkeypatch.setattr(warm, "relay_attended", relay)
    tool_fn = _tool_fn(cse_session_module)
    result = tool_fn(op="resolve_attended")
    assert result["code"] == "no_attended_cse"
    assert result["retryable"] is True
    assert result["source"] == "gateway"


def test_followup_no_prompt(
    cse_session_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tools.cse_session_warm as warm

    monkeypatch.setattr(warm, "_relay", MagicMock())
    tool_fn = _tool_fn(cse_session_module)
    result = tool_fn(op="followup", registration_id="reg-1")
    assert result == {"ok": False, "error": "no_prompt"}
