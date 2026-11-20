"""The interlocutor read docstring routes claude.ai chat URLs to chat_session."""

from __future__ import annotations

import inspect

import interlocutor.mcp_tool


def test_read_docstring_routes_chat_urls_to_chat_session() -> None:
    """The read paragraph names chat_session and drops the harvest refusal."""
    source = inspect.getsource(interlocutor.mcp_tool)
    assert "use_chat_session" in source
    assert "chat_session(" in source
    assert "same refusal" not in source
    assert "do not use on another" not in source
