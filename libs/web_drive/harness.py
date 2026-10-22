"""Harness gate for Messages send.

The interface includes send. This module is the policy in front of it:
do not send until the operator authorizes that agent for all messages,
or for subsequent messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SendScope = Literal["all", "subsequent"]


class SendNotAuthorizedError(Exception):
    """The harness refused a send."""


@dataclass(frozen=True)
class SendAuthorization:
    """Operator grant for one agent. Not a field of the read interface."""

    agent: str
    scope: SendScope


def require_send_authorization(
    authorization: SendAuthorization | None,
    agent: str,
) -> SendAuthorization:
    """Return the grant, or refuse before any Messages page action."""
    if (
        authorization is None
        or authorization.agent != agent
        or authorization.scope not in ("all", "subsequent")
    ):
        raise SendNotAuthorizedError(
            "harness: no send until this agent is authorized for all "
            "messages, or for subsequent messages"
        )
    return authorization
