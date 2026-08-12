"""MCP relay tests for project_ask op=followup."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_MCP_SERVER = Path(__file__).resolve().parents[3] / "services" / "mcp-server"
if str(_MCP_SERVER) not in sys.path:
    sys.path.insert(0, str(_MCP_SERVER))


@pytest.fixture
def project_ask_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PROJECT_ASK_URL", "http://satellite:8770")
    import tools.project_ask as mod

    return importlib.reload(mod)


def _tool_fn(mod):
    captured: list = []

    class _FakeMcp:
        def tool(self, **_kwargs):
            def _decorator(fn):
                captured.append(fn)
                return fn

            return _decorator

    mod.register_project_ask_tool(_FakeMcp())
    assert captured, "project_ask tool not registered"
    return captured[0]


def test_followup_relay_timeout_uses_caller_budget(
    project_ask_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = MagicMock(
        return_value={
            "ok": True,
            "url": "https://claude.ai/cowork/cse_x",
            "send_verified": True,
        }
    )
    monkeypatch.setattr(project_ask_module, "_relay", relay)
    recorded: list[dict] = []
    monkeypatch.setattr(
        project_ask_module,
        "record",
        lambda signal, **kwargs: recorded.append({"signal": signal, **kwargs}),
    )
    tool_fn = _tool_fn(project_ask_module)
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
    assert recorded[0]["signal"] == "mcp.project_ask.followup"


def test_followup_no_playwright_imports_in_module(project_ask_module) -> None:
    source = project_ask_module.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")
    import_lines = [
        line
        for line in text.splitlines()
        if line.startswith("import ") or line.startswith("from ")
    ]
    joined = "\n".join(import_lines)
    assert "playwright" not in joined
    assert "claude_bundles" not in joined


def test_followup_identity_omitted_relays_to_satellite(
    project_ask_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = MagicMock(return_value={"ok": False, "error": "no_attended_cse"})
    monkeypatch.setattr(project_ask_module, "_relay", relay)
    tool_fn = _tool_fn(project_ask_module)
    result = tool_fn(op="followup", prompt_text="x")
    relay.assert_called_once()
    assert result["error"] == "no_attended_cse"


def test_followup_forwards_cdp_url(
    project_ask_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = MagicMock(return_value={"ok": True, "send_verified": True})
    monkeypatch.setattr(project_ask_module, "_relay", relay)
    tool_fn = _tool_fn(project_ask_module)
    tool_fn(
        op="followup",
        chat_url="https://claude.ai/cowork/cse_x",
        cdp_url="http://127.0.0.1:9225",
        prompt_text="wake",
    )
    body = relay.call_args.kwargs["json_body"]
    assert body["cdp_url"] == "http://127.0.0.1:9225"


def test_resolve_attended_maps_404(
    project_ask_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    relay = MagicMock(
        return_value={
            "code": "no_attended_cse",
            "message": "No mission-purpose attended CSE registered with bound chat_url",
            "source": "gateway",
            "retryable": True,
            "data": {"candidates_considered": 0, "shadow_urls": []},
        }
    )
    monkeypatch.setattr(project_ask_module, "_relay_attended", relay)
    tool_fn = _tool_fn(project_ask_module)
    result = tool_fn(op="resolve_attended")
    assert result["code"] == "no_attended_cse"
    assert result["retryable"] is True
    assert result["source"] == "gateway"


def test_followup_no_prompt(
    project_ask_module, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(project_ask_module, "_relay", MagicMock())
    tool_fn = _tool_fn(project_ask_module)
    result = tool_fn(op="followup", registration_id="reg-1")
    assert result == {"ok": False, "error": "no_prompt"}
