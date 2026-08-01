"""Life MCP notify tool — kill-switch, ref degrade, event emit."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from request_profile import bind_request
from tools.notify import (
    append_ref_to_body,
    normalize_ref,
    register_notify_tools,
)

pytestmark = pytest.mark.offline


class _CaptureMCP:
    def __init__(self) -> None:
        self.fn = None

    def tool(self, *args: object, **kwargs: object):  # noqa: ANN204
        def decorator(fn: object) -> object:
            self.fn = fn
            return fn

        if args and callable(args[0]) and not kwargs:
            self.fn = args[0]
            return args[0]
        return decorator


def _notify_fn():
    cap = _CaptureMCP()
    register_notify_tools(cap)
    assert cap.fn is not None
    return cap.fn


def test_normalize_ref_degrades_missing() -> None:
    ref, unreferenced = normalize_ref("  ")
    assert ref == "(unreferenced)"
    assert unreferenced is True


def test_normalize_ref_preserves_value() -> None:
    ref, unreferenced = normalize_ref("agent-bus:6231")
    assert ref == "agent-bus:6231"
    assert unreferenced is False


def test_append_ref_to_body_respects_cap() -> None:
    from pager_notify.so_what import SMS_BODY_MAX

    body = "x" * (SMS_BODY_MAX - 20)
    out = append_ref_to_body(body, "agent-bus:1")
    assert len(out) <= SMS_BODY_MAX
    assert out.startswith(body)
    assert "agent-bus:1" in out


def test_append_ref_to_body_prefers_full_body() -> None:
    from pager_notify.so_what import SMS_BODY_MAX

    body = "y" * SMS_BODY_MAX
    out = append_ref_to_body(body, "agent-bus:1")
    assert out == body
    assert len(out) == SMS_BODY_MAX


def test_notify_kill_switch() -> None:
    notify = _notify_fn()
    with bind_request("default", surface="life"):
        with patch("pager_notify.life_notify.pager_enabled", return_value=False):
            result = notify(
                subject="ULG test",
                body="disabled path",
                ref="agent-bus:1",
            )
    assert result["status"] == "disabled"
    assert result["reason"] == "PAGER_NOTIFY_ENABLED=0"
    assert result["from_agent"] == "web-anthropic"
    assert result["ref"] == "agent-bus:1"


def test_notify_unreferenced_and_event_emit() -> None:
    notify = _notify_fn()
    mock_pager = AsyncMock(return_value=True)
    with bind_request("default", surface="life"):
        with (
            patch("pager_notify.life_notify.pager_enabled", return_value=True),
            patch("pager_notify.life_notify.notify_pager", mock_pager),
            patch("pager_notify.life_notify._default_record") as mock_record,
        ):
            result = notify(
                subject="ULG arc closed",
                body="Done — bind shipped",
                tag="arc-close",
            )
    assert result["status"] == "sent"
    assert result["unreferenced"] is True
    assert result["ref"] == "(unreferenced)"
    mock_pager.assert_awaited_once()
    mock_record.assert_called_once()
    assert mock_record.call_args.args[0] == "ops.notify.sent"
    assert mock_record.call_args.kwargs["unreferenced"] is True
