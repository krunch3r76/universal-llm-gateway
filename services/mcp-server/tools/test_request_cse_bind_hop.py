"""Paste or request bind must not associate a retired URL over a hop successor."""

from __future__ import annotations

from unittest.mock import patch

from agent_bus_store.db.cse_associations import HOP_SEATED_BOUND_BY

from tools.agent_bus.request_cse_bind import maybe_bind_thread_cse

_SUCCESSOR = "https://claude.ai/cowork/cse_01Cir4NQUXr8sPrwmGJhXyak"
_RETIRED = "https://claude.ai/cowork/cse_017cw5A7geCQNPPP7LzB78vB"


def test_retired_url_does_not_call_associate_when_hop_successor_is_current() -> None:
    """Breaks when the GET is skipped, bound_by is compared to the relay name,
    or a differing URL still POSTs cse-associate.
    """

    def _relay(service: str, method: str, path: str, **kwargs: object) -> dict:
        assert method == "GET"
        assert path.endswith("/cse-current")
        return {
            "state": "associated",
            "cse_chat_url": _SUCCESSOR,
            "cse_registration_id": "reg-successor",
            "bound_by": HOP_SEATED_BOUND_BY,
        }

    with patch("tools.agent_bus.request_cse_bind.relay", side_effect=_relay) as relay:
        result = maybe_bind_thread_cse(
            thread_id="12286",
            from_agent="web-anthropic",
            cse_chat_url=_RETIRED,
            cse_registration_id="reg-retired",
        )
    assert result is None
    assert all(call.args[1] != "POST" for call in relay.call_args_list)


def test_cse_current_down_still_attempts_post() -> None:
    """GET failure does not block. The store refuses a mint-row rewrite if the POST lands.

    Breaks when a downed cse-current read is treated as 'hop successor held'
    and a first bind never posts.
    """
    posts: list[str] = []

    def _relay(service: str, method: str, path: str, **kwargs: object) -> dict:
        if method == "GET":
            raise ConnectionError("agent-bus down")
        posts.append(path)
        return {"state": "associated"}

    with patch("tools.agent_bus.request_cse_bind.relay", side_effect=_relay):
        result = maybe_bind_thread_cse(
            thread_id="12286",
            from_agent="web-anthropic",
            cse_chat_url=_RETIRED,
            cse_registration_id="reg-retired",
        )
    assert result == {"state": "associated"}
    assert posts == ["/threads/12286/cse-associate"]
