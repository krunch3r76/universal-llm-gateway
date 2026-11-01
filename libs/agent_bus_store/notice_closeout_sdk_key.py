"""Shared sdk CLOSEOUT subject parsing for runtime dedupe and migration backfill."""

from __future__ import annotations

import re

_SDK_CLOSEOUT_SUBJECT_RE = re.compile(
    r"^(?:CLOSEOUT\|)?cursor-sdk CLOSEOUT (?P<dispatch_id>\S+)",
    re.IGNORECASE,
)


def sdk_closeout_dispatch_id(subject: str) -> str | None:
    """Dispatch id token from subject; None when not an sdk CLOSEOUT subject."""
    match = _SDK_CLOSEOUT_SUBJECT_RE.match((subject or "").strip())
    if match is None:
        return None
    return match.group("dispatch_id")


def sdk_notice_closeout_key(*, from_agent: str, subject: str) -> str | None:
    """Durable insert key ``sdk:<dispatch_id>`` for cursor-sdk CLOSEOUT sends."""
    if from_agent != "cursor-sdk":
        return None
    dispatch_id = sdk_closeout_dispatch_id(subject)
    if not dispatch_id:
        return None
    return f"sdk:{dispatch_id}"


__all__ = ["sdk_closeout_dispatch_id", "sdk_notice_closeout_key"]
