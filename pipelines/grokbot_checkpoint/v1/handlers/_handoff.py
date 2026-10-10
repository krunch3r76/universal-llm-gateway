"""Successor handoff letter: shape check and pour binding."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

HANDOFF_MAX_BYTES = 16 * 1024

_SHA64 = re.compile(r"^[0-9a-fA-F]{64}$")
_HOUSE_CP = re.compile(r"^house-cp:\S+:[1-9][0-9]*$")


def handoff_canonical_bytes(letter: dict[str, Any]) -> bytes:
    return json.dumps(
        letter, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def handoff_sha12(letter: dict[str, Any] | None) -> str:
    if not isinstance(letter, dict):
        return "none"
    return hashlib.sha256(handoff_canonical_bytes(letter)).hexdigest()[:12]


def handoff_json_size(letter: object) -> int:
    raw = json.dumps(letter, ensure_ascii=False, separators=(",", ":"))
    return len(raw.encode())


def validate_handoff(letter: object) -> tuple[str, str] | None:
    """Return ``(code, message)`` for the first defect, or None when the letter is admissible."""
    if not isinstance(letter, dict):
        return "admit.handoff_invalid", "handoff"
    if handoff_json_size(letter) > HANDOFF_MAX_BYTES:
        return "admit.handoff_too_large", "handoff exceeds 16 KiB"
    house_cp = letter.get("house_cp")
    if not isinstance(house_cp, str) or not _HOUSE_CP.fullmatch(house_cp):
        return "admit.handoff_invalid", "house_cp"
    digest = letter.get("messages_sha256")
    if not isinstance(digest, str) or not _SHA64.fullmatch(digest):
        return "admit.handoff_invalid", "messages_sha256"
    index = letter.get("as_of_message_index")
    if isinstance(index, bool) or not isinstance(index, int) or index < 1:
        return "admit.handoff_invalid", "as_of_message_index"
    author = letter.get("author")
    if not isinstance(author, str) or not author.strip():
        return "admit.handoff_invalid", "author"
    mission = letter.get("mission")
    if (
        not isinstance(mission, str)
        or not mission
        or "\n" in mission
        or "\r" in mission
    ):
        return "admit.handoff_invalid", "mission"
    state = letter.get("state")
    if not isinstance(state, str):
        return "admit.handoff_invalid", "state"
    acts = letter.get("next_acts")
    if (
        not isinstance(acts, list)
        or not acts
        or any(not isinstance(item, str) for item in acts)
    ):
        return "admit.handoff_invalid", "next_acts"
    bearing = letter.get("load_bearing")
    if not isinstance(bearing, list) or any(
        not isinstance(item, str) for item in bearing
    ):
        return "admit.handoff_invalid", "load_bearing"
    if not bearing:
        reason = letter.get("load_bearing_reason")
        if not isinstance(reason, str) or not reason.strip():
            return "admit.handoff_invalid", "load_bearing"
    later = letter.get("later", None)
    if "later" in letter and not isinstance(later, (str, list)):
        return "admit.handoff_invalid", "later"
    return None


def handoff_binding(
    letter: dict[str, Any] | None,
    *,
    messages: list[Any],
    messages_sha256: str,
    house: str,
    n: int,
) -> tuple[str, list[str]]:
    """``bound`` when the letter names this pour; ``stale`` names each failed leg."""
    if letter is None:
        return "missing", []
    reasons: list[str] = []
    if letter.get("messages_sha256") != messages_sha256:
        reasons.append("messages_sha256")
    if letter.get("as_of_message_index") != len(messages):
        reasons.append("as_of_message_index")
    if letter.get("house_cp") != f"house-cp:{house}:{n}":
        reasons.append("house_cp")
    if reasons:
        return "stale", reasons
    return "bound", []
