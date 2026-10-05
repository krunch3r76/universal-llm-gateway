"""Request bind posts; the store holds the hop successor across a same-URL append."""

from __future__ import annotations

from unittest.mock import patch

from tools.agent_bus.request_cse_bind import maybe_bind_thread_cse

_RETIRED = "https://claude.ai/cowork/cse_017cw5A7geCQNPPP7LzB78vB"


def test_retired_url_posts_and_returns_store_unchanged() -> None:
    """A retired URL is posted. The store, not a newest-row pre-check, returns unchanged.

    Breaks when a local pre-check skips the POST after reading cse-current.
    That check keys off the newest bound_by, so a same-URL append clears it
    and a retired paste is associated. Agent-bus down is the POST failing
    soft, covered separately.
    """

    def _relay(service: str, method: str, path: str, **kwargs: object) -> dict:
        assert method == "POST"
        assert path == "/threads/12286/cse-associate"
        body = kwargs.get("body")
        assert isinstance(body, dict)
        assert body["cse_chat_url"] == _RETIRED
        assert body["bound_by"] == "web-anthropic"
        return {"thread_id": "12286", "state": "unchanged"}

    with patch("tools.agent_bus.request_cse_bind.relay", side_effect=_relay) as relay:
        result = maybe_bind_thread_cse(
            thread_id="12286",
            from_agent="web-anthropic",
            cse_chat_url=_RETIRED,
            cse_registration_id="reg-retired",
        )
    assert result == {"thread_id": "12286", "state": "unchanged"}
    assert relay.call_count == 1


def test_associate_post_down_returns_none() -> None:
    """A downed associate POST returns None and does not raise.

    Breaks when agent-bus being down is treated as a successful bind, or when
    the relay error escapes the request path. There is no cse-current read
    that can false-block a first bind.
    """

    def _relay(service: str, method: str, path: str, **kwargs: object) -> dict:
        assert method == "POST"
        raise ConnectionError("agent-bus down")

    with patch("tools.agent_bus.request_cse_bind.relay", side_effect=_relay):
        result = maybe_bind_thread_cse(
            thread_id="12286",
            from_agent="web-anthropic",
            cse_chat_url=_RETIRED,
            cse_registration_id="reg-retired",
        )
    assert result is None
