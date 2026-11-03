"""agent_bus omitted author is the literal unspecified, plus a warning."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_bus_store.author_attestation import attest, reset_peer_node_cache_for_tests
from request_profile import bind_request

from tools import agent_bus as agent_bus_module
from tools._agent_bus_author import (
    AUTHOR_OMITTED_WARNING,
    AUTHOR_OMITTED_WARNING_KEY,
    UNSPECIFIED_AUTHOR,
    default_from_for_surface,
    reconcile_author_arguments,
    turn_author_attestation_fields,
)


def test_default_from_for_surface_map() -> None:
    """Boot and dispatch-caller still use the surface map; bus omit does not."""
    assert default_from_for_surface("life") == "web-anthropic"
    assert default_from_for_surface("code") == "cursor"
    assert default_from_for_surface("unknown") is None
    assert default_from_for_surface(None) is None


def test_reconcile_from_alias_wins_over_autofill() -> None:
    with bind_request("default", surface="code"):
        args, err = reconcile_author_arguments({"from": "web-anthropic", "thread": "1"})
    assert err is None
    assert args["from_agent"] == "web-anthropic"
    assert "from" not in args
    assert AUTHOR_OMITTED_WARNING_KEY not in args


def test_reconcile_explicit_from_agent_wins() -> None:
    with bind_request("default", surface="code"):
        args, err = reconcile_author_arguments(
            {"from_agent": "grok-web", "from": "cursor"}
        )
    assert err is None
    assert args["from_agent"] == "grok-web"
    assert AUTHOR_OMITTED_WARNING_KEY not in args


def test_reconcile_explicit_agent_alias_unchanged() -> None:
    """Breaks when an explicit agent= is rewritten to unspecified."""
    args, err = reconcile_author_arguments({"agent": "cursor", "thread": "1"})
    assert err is None
    assert args["from_agent"] == "cursor"
    assert "agent" not in args
    assert AUTHOR_OMITTED_WARNING_KEY not in args


@pytest.mark.parametrize("surface", ["life", "code", "unknown", None])
def test_reconcile_blank_author_is_unspecified(surface: str | None) -> None:
    """Breaks when a blank author borrows a seat or 422s on an unknown surface."""
    with bind_request("default", surface=surface):
        args, err = reconcile_author_arguments({"thread": "99"})
    assert err is None
    assert args["from_agent"] == UNSPECIFIED_AUTHOR
    assert args[AUTHOR_OMITTED_WARNING_KEY] == AUTHOR_OMITTED_WARNING


def test_reconcile_blank_author_without_request_context() -> None:
    """Breaks when a missing request surface still returns from_agent_required."""
    args, err = reconcile_author_arguments({"thread": "99", "from_agent": "  "})
    assert err is None
    assert args["from_agent"] == UNSPECIFIED_AUTHOR
    assert "from" in args[AUTHOR_OMITTED_WARNING_KEY]
    assert "from_agent" in args[AUTHOR_OMITTED_WARNING_KEY]


def test_send_dispatch_autofills_on_code_surface() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
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
    assert relay_calls[0]["from"] == UNSPECIFIED_AUTHOR
    assert "warning" not in relay_calls[0]
    assert result["warning"] == AUTHOR_OMITTED_WARNING
    assert "from" in result["warning"]
    assert "from_agent" in result["warning"]


def test_reply_dispatch_accepts_from_alias() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
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


def test_attest_known_and_unknown_peer_ip() -> None:
    reset_peer_node_cache_for_tests()
    known = attest(
        "static",
        "cursor",
        "code",
        client_ip="10.0.0.76",
        user_agent="Mozilla/5.0 Cursor",
    )
    assert known == "static:cursor@code via=jupiter(10.0.0.76) client=cursor-ide"
    unknown = attest("static", "cursor", "code", client_ip="203.0.113.9")
    assert unknown == "static:cursor@code via=unknown(203.0.113.9)"


def test_send_stamps_attestation_explicit_from() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        relay_calls.append(kwargs.get("body") or {})
        return {
            "send_path": "new_thread",
            "thread": {"id": "501"},
            "turn": {"turn_number": 1},
        }

    with bind_request(
        "default",
        surface="code",
        auth_mode="static",
        caller_identity="cursor",
        user_agent="python-httpx/0.27",
        client_ip="10.0.0.76",
    ):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                result = agent_bus_module._send_dispatch(
                    new_slug="attest-explicit",
                    to="web-anthropic",
                    subject="hi",
                    body="brief",
                    from_agent="cursor",
                )

    assert "error" not in result
    body = relay_calls[0]
    assert body["from"] == "cursor"
    assert "warning" not in result
    assert (
        body["attested_by"]
        == "static:cursor@code via=jupiter(10.0.0.76) client=ulg-local"
    )
    assert body["user_agent"] == "python-httpx/0.27"


def test_send_stamps_attestation_on_surface_autofill() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        relay_calls.append(kwargs.get("body") or {})
        return {
            "send_path": "new_thread",
            "thread": {"id": "502"},
            "turn": {"turn_number": 1},
        }

    with bind_request(
        "default",
        surface="code",
        auth_mode="static",
        caller_identity="cursor",
    ):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                result = agent_bus_module._send_dispatch(
                    new_slug="attest-autofill",
                    to="web-anthropic",
                    subject="hi",
                    body="brief",
                )

    assert "error" not in result
    body = relay_calls[0]
    assert body["from"] == UNSPECIFIED_AUTHOR
    assert body["attested_by"] == "static:cursor@code"
    assert "user_agent" not in body
    assert result["warning"] == AUTHOR_OMITTED_WARNING


def test_send_succeeds_without_attestation_triple() -> None:
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        relay_calls.append(kwargs.get("body") or {})
        return {
            "send_path": "new_thread",
            "thread": {"id": "503"},
            "turn": {"turn_number": 1},
        }

    with bind_request("default", surface="code"):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                result = agent_bus_module._send_dispatch(
                    new_slug="attest-null",
                    to="web-anthropic",
                    subject="hi",
                    body="brief",
                )

    assert "error" not in result
    body = relay_calls[0]
    assert body["from"] == UNSPECIFIED_AUTHOR
    assert "attested_by" not in body
    assert result["warning"] == AUTHOR_OMITTED_WARNING
    with bind_request("default", surface="code"):
        attested, ua = turn_author_attestation_fields()
    assert attested is None
    assert ua is None


def test_post_and_reply_omit_author_warn_on_success() -> None:
    """Breaks when post or reply drops the warning or borrows a surface seat."""
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        relay_calls.append(kwargs.get("body") or {})
        if path.endswith("/with-turn"):
            return {"thread": {"id": "70"}, "turn": {"turn_number": 1}}
        return {"turn_number": 2, "id": 71}

    with bind_request("default", surface="life"):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                posted = agent_bus_module._post_dispatch(
                    slug="omit-post",
                    to="cursor",
                    subject="hi",
                    body="brief",
                )
                replied = agent_bus_module._reply_dispatch(
                    thread="70",
                    to="cursor",
                    subject="Re",
                    body="ok",
                )

    assert "error" not in posted
    assert "error" not in replied
    assert relay_calls[0]["from"] == UNSPECIFIED_AUTHOR
    assert relay_calls[1]["from"] == UNSPECIFIED_AUTHOR
    assert posted["warning"] == AUTHOR_OMITTED_WARNING
    assert replied["warning"] == AUTHOR_OMITTED_WARNING


def test_send_explicit_unspecified_has_no_warning() -> None:
    """Breaks when a caller who wrote unspecified is warned as if they omitted it."""
    relay_calls: list[dict[str, Any]] = []

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        relay_calls.append(kwargs.get("body") or {})
        return {
            "send_path": "new_thread",
            "thread": {"id": "504"},
            "turn": {"turn_number": 1},
        }

    with bind_request("default", surface="code"):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                result = agent_bus_module._send_dispatch(
                    new_slug="explicit-unspecified",
                    to="web-anthropic",
                    subject="hi",
                    body="brief",
                    from_agent=UNSPECIFIED_AUTHOR,
                )

    assert "error" not in result
    assert "warning" not in result
    assert relay_calls[0]["from"] == UNSPECIFIED_AUTHOR


def test_send_omit_author_relay_error_has_no_warning() -> None:
    """Breaks when a down relay still returns the omit warning as a success."""

    def _relay(service: str, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return {"error": "connection refused"}

    with bind_request("default", surface="code"):
        with patch.object(agent_bus_module, "_relay", side_effect=_relay):
            with patch.object(agent_bus_module, "record", lambda *_a, **_k: None):
                result = agent_bus_module._send_dispatch(
                    new_slug="relay-down",
                    to="web-anthropic",
                    subject="hi",
                    body="brief",
                )

    assert "error" in result
    assert "warning" not in result
