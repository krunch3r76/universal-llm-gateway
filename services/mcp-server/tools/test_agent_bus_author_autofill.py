"""agent_bus from= surface autofill."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_profile import bind_request
from tools import agent_bus as agent_bus_module
from tools._agent_bus_author import (
    default_from_for_surface,
    reconcile_author_arguments,
)


def test_default_from_for_surface_map() -> None:
    assert default_from_for_surface("life") == "web-anthropic"
    assert default_from_for_surface("code") == "cursor"
    assert default_from_for_surface("unknown") is None
    assert default_from_for_surface(None) is None


def test_reconcile_from_alias_wins_over_autofill() -> None:
    with bind_request("default", surface="code"):
        args, err = reconcile_author_arguments(
            {"from": "web-anthropic", "thread": "1"}
        )
    assert err is None
    assert args["from_agent"] == "web-anthropic"
    assert "from" not in args


def test_reconcile_explicit_from_agent_wins() -> None:
    with bind_request("default", surface="code"):
        args, err = reconcile_author_arguments(
            {"from_agent": "grok-web", "from": "cursor"}
        )
    assert err is None
    assert args["from_agent"] == "grok-web"


@pytest.mark.parametrize(
    ("surface", "expected"),
    [("life", "web-anthropic"), ("code", "cursor")],
)
def test_reconcile_autofill_from_surface(surface: str, expected: str) -> None:
    with bind_request("default", surface=surface):
        args, err = reconcile_author_arguments({"thread": "99"})
    assert err is None
    assert args["from_agent"] == expected


def test_reconcile_missing_author_without_surface() -> None:
    args, err = reconcile_author_arguments({"thread": "99"})
    assert err is not None
    assert err["reason"] == "from_agent_required"
    assert "from_agent" in err["missing_fields"]
    assert args.get("from_agent") in (None, "")


def test_send_dispatch_autofills_on_code_surface() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        assert service == "agent-bus"
        relay_calls.append(kwargs.get("body") or {})
        return {
            "send_path": "new_thread",
            "thread": {"id": "500"},
            "turn": {"turn_number": 1},
        }

    with bind_request("default", surface="code"):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                result = agent_bus_module._send_dispatch(
                    new_slug="autofill-test",
                    to="web-anthropic",
                    subject="hi",
                    body="brief",
                )

    assert "error" not in result
    assert relay_calls[0]["from"] == "cursor"


def test_reply_dispatch_accepts_from_alias() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(
        service: str, method: str, path: str, **kwargs: Any
    ) -> dict[str, Any]:
        relay_calls.append(kwargs.get("body") or {})
        return {"turn_number": 2, "id": 42}

    with bind_request("default", surface="life"):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                parsed, err = reconcile_author_arguments(
                    {
                        "thread": "111",
                        "to": "cursor",
                        "subject": "Re",
                        "body": "ok",
                        "from": "web-anthropic",
                    }
                )
                assert err is None
                result = agent_bus_module._reply_dispatch(**parsed)

    assert "error" not in result
    assert relay_calls[0]["from"] == "web-anthropic"
