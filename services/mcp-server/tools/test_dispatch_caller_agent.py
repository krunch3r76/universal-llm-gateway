"""Mount-aware caller_agent inference for conductor team_dispatch."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_profile import bind_request

from tools._dispatch_caller_agent import infer_caller_agent_for_conductor


def test_infer_conductor_code_mount_stamps_cursor() -> None:
    # 19ab1566a: kw-only packet_kind renamed to contract. Blank caller +
    # contract other than "conductor" returns None (ordering / omitted contract).
    with bind_request("default", surface="code"):
        assert (
            infer_caller_agent_for_conductor(None, contract="conductor")
            == "cursor"
        )


def test_infer_conductor_life_mount_stamps_web_anthropic() -> None:
    with bind_request("default", surface="life"):
        assert (
            infer_caller_agent_for_conductor(None, contract="conductor")
            == "web-anthropic"
        )


def test_infer_explicit_caller_wins() -> None:
    with bind_request("default", surface="code"):
        assert (
            infer_caller_agent_for_conductor("cursor-auto", contract="conductor")
            == "cursor-auto"
        )


def test_infer_non_conductor_returns_none() -> None:
    with bind_request("default", surface="code"):
        assert infer_caller_agent_for_conductor(None, contract=None) is None
        assert infer_caller_agent_for_conductor(None, contract="implement") is None


def test_infer_unknown_surface_returns_none() -> None:
    with bind_request("default", surface="unknown"):
        assert (
            infer_caller_agent_for_conductor(None, contract="conductor") is None
        )
