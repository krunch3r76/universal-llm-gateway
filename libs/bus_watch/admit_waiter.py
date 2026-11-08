"""Parse coord/API/CDP generate admit subjects and waiter stamps (agent_bus-local)."""

from __future__ import annotations

import re

COORD_GENERATE_ADMIT_SUBJECT = "cursor-sdk generate admitted"
PROVENANCE_MISSING_PREFIX = "PROVENANCE_MISSING:"
_CDP_GENERATE_ADMIT_RE = re.compile(
    r"^cdp generate — .+$",
    re.IGNORECASE,
)
_ROLE_GENERATE_ADMIT_RE = re.compile(
    r"^.+ generate — .+$",
    re.IGNORECASE,
)
_WAITER_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def coord_admit_subject_matches(subject: str | None) -> bool:
    """True for coord/API/CDP generate admit pointer subjects."""
    text = (subject or "").strip()
    if not text:
        return False
    head = COORD_GENERATE_ADMIT_SUBJECT
    if text == head or text.startswith(f"{head}|") or text.startswith(f"{head} "):
        return True
    if _CDP_GENERATE_ADMIT_RE.match(text):
        return True
    if _ROLE_GENERATE_ADMIT_RE.match(text):
        return True
    return False


def parse_waiter_from_admit_body(
    body: str | None,
    *,
    subject: str | None,
) -> str | None:
    """First UUID ``waiter:`` in the leading header block of an admit turn body."""
    if not body or not coord_admit_subject_matches(subject):
        return None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith(("work_key:", PROVENANCE_MISSING_PREFIX.lower())):
            continue
        if lower.startswith("waiter:"):
            value = stripped.split(":", 1)[1].strip()
            if _WAITER_UUID_RE.match(value):
                return value
            return None
        break
    return None
