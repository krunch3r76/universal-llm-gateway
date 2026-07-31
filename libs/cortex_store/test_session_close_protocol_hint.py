"""session_close dispatch carries mandatory _protocol pointer."""

from __future__ import annotations

from cortex_store.dispatch_ops.workflow_hints import attach_session_close_protocol


def test_attach_session_close_protocol_on_close_tools() -> None:
    for tool in ("session_close", "session_close_preflight"):
        result: dict[str, str] = {}
        attach_session_close_protocol(result, tool)
        assert "_protocol" in result
        assert "session-close-kernel" in result["_protocol"]
        assert "Life/web primary: close(op=stage|draft|check|commit)" in result["_protocol"]
        assert "agent-skills/" not in result["_protocol"]
        assert "fs(" not in result["_protocol"]
        assert "Load before close" in result["_protocol"]


def test_attach_session_close_protocol_skips_other_ops() -> None:
    result: dict[str, str] = {}
    attach_session_close_protocol(result, "entity_get")
    assert "_protocol" not in result
