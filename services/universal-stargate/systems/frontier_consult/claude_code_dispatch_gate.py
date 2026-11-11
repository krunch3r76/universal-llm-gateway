"""Refuse claude.ai/code dispatch when route policy prohibits it."""

from __future__ import annotations

from typing import Any

from implement_admission.claude_code_dispatch import (
    claude_code_dispatch_prohibited,
    claude_code_dispatch_ruling_uri,
    claude_code_dispatch_toggle_key,
    is_claude_code_surface,
    load_claude_code_dispatch_mode,
)
from universal_protocol.errors import ProtocolError

from .admission import FrontierEndpointError
from .cursor_sdk_generate_signals import publish_frontier_event
from .events import DispatchClaudecodeRefused

__all__ = ["reject_claude_code_dispatch_if_prohibited"]


def _protocol_envelope(*, toggle_value: str) -> dict[str, Any]:
    key = claude_code_dispatch_toggle_key()
    ruling = claude_code_dispatch_ruling_uri()
    message = (
        f"claude-code dispatch is prohibited "
        f"(config {key}={toggle_value}; ruling {ruling})"
    )
    return ProtocolError(
        code="CLAUDE_CODE_DISPATCH_PROHIBITED",
        message=message,
        source="stargate",
        retryable=False,
        data={
            "toggle": key,
            "toggle_value": toggle_value,
            "ruling_uri": ruling,
        },
    ).to_dict()


def reject_claude_code_dispatch_if_prohibited(
    *,
    request_id: str,
    claude_surface: str | None,
    model: str | None,
    entry: str = "team_dispatch_cdp_generate",
) -> None:
    """Raise 422 when ``claude_surface=code`` and policy mode is ``prohibit``."""
    if not is_claude_code_surface(claude_surface):
        return
    if not claude_code_dispatch_prohibited():
        return
    toggle_value = load_claude_code_dispatch_mode()
    publish_frontier_event(
        DispatchClaudecodeRefused(
            request_id=request_id,
            claude_surface="code",
            model=model,
            entry=entry,
            code="CLAUDE_CODE_DISPATCH_PROHIBITED",
        )
    )
    envelope = _protocol_envelope(toggle_value=toggle_value)
    raise FrontierEndpointError(
        request_id=request_id,
        field="claude_surface",
        reason=str(envelope["message"]),
        status_code=422,
        code="CLAUDE_CODE_DISPATCH_PROHIBITED",
        details=envelope,
    )
