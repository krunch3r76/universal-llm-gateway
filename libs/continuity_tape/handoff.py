"""Successor handoff letter: shape check, pour binding, and seal stamp."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

HANDOFF_MAX_BYTES = 16 * 1024

_SHA64 = re.compile(r"^[0-9a-fA-F]{64}$")
_HOUSE_CP = re.compile(r"^house-cp:\S+:[1-9][0-9]*$")
_HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")

_PIPELINE_OWNED = (
    "session_id",
    "messages_sha256",
    "as_of_message_index",
    "house",
    "transcript_id",
    "surface",
    "stamped_at",
    "turns_at_seal",
    "house_cp",
)

_OPTIONAL_STR_OR_LIST = (
    "in_flight",
    "rulings",
    "files",
    "later",
    "do_not_trust",
)


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


def bare_messages_sha(value: object) -> str | None:
    """Bare hex64 messages digest. A ``sha256:`` prefix or any other shape is absent."""
    if isinstance(value, str) and _HEX64.fullmatch(value):
        return value.lower()
    return None


def _prefix(profile: str) -> str:
    return "admit" if profile == "grok" else "checkpoint"


def _str_or_str_list(value: object) -> bool:
    if isinstance(value, str):
        return True
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return True
    return False


def _flat(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " | ".join(f"{k}: {_flat(v)}" for k, v in value.items())
    if isinstance(value, list):
        return " | ".join(_flat(item) for item in value)
    return json.dumps(value, ensure_ascii=False)


_STRING_FIELDS = ("author", "mission", "state", "where_we_left_off")
_LIST_FIELDS = ("next_acts", "load_bearing")


def normalize_handoff(letter: object) -> tuple[dict[str, Any], list[str]]:
    """Return a normalised copy and the field names that changed.

    Pure: ``letter`` is not mutated. A second call on the result reports no
    fields. String fields flatten dicts and lists. ``mission`` newlines become
    spaces only when the incoming value was not a string. List fields wrap a
    bare string and stringify non-string items. An empty list stays empty.
    """
    if not isinstance(letter, dict):
        return {}, []
    out = dict(letter)
    fields: list[str] = []
    for key in _STRING_FIELDS:
        if key not in out or isinstance(out[key], str):
            continue
        text = _flat(out[key])
        if key == "mission":
            text = text.replace("\r", " ").replace("\n", " ")
        out[key] = text
        fields.append(key)
    for key in _OPTIONAL_STR_OR_LIST:
        if key not in out or not isinstance(out[key], dict):
            continue
        out[key] = _flat(out[key])
        fields.append(key)
    for key in _LIST_FIELDS:
        if key not in out:
            continue
        value = out[key]
        if isinstance(value, str):
            out[key] = [value]
            fields.append(key)
            continue
        if isinstance(value, list) and any(not isinstance(item, str) for item in value):
            out[key] = [_flat(item) for item in value]
            fields.append(key)
    return out, fields


def _author_fields(letter: dict[str, Any], code: str) -> tuple[str, str] | None:
    author = letter.get("author")
    if not isinstance(author, str) or not author.strip():
        return code, "author"
    mission = letter.get("mission")
    if (
        not isinstance(mission, str)
        or not mission
        or "\n" in mission
        or "\r" in mission
    ):
        return code, "mission"
    where = letter.get("where_we_left_off")
    if not isinstance(where, str) or not where.strip() or len(where) > 600:
        return code, "where_we_left_off"
    state = letter.get("state")
    if not isinstance(state, str):
        return code, "state"
    acts = letter.get("next_acts")
    if (
        not isinstance(acts, list)
        or not acts
        or any(not isinstance(item, str) for item in acts)
    ):
        return code, "next_acts"
    bearing = letter.get("load_bearing")
    if not isinstance(bearing, list) or any(
        not isinstance(item, str) for item in bearing
    ):
        return code, "load_bearing"
    if not bearing:
        reason = letter.get("load_bearing_reason")
        if not isinstance(reason, str) or not reason.strip():
            return code, "load_bearing"
    for key in _OPTIONAL_STR_OR_LIST:
        if key in letter and not _str_or_str_list(letter.get(key)):
            return code, key
    if "as_of_turn" in letter:
        turn = letter.get("as_of_turn")
        if isinstance(turn, bool) or not isinstance(turn, int) or turn < 1:
            return code, "as_of_turn"
    return None


def validate_handoff(
    letter: object, *, profile: str = "grok"
) -> tuple[str, str] | None:
    """Return ``(code, field)`` for the first defect, or None when admissible.

    ``profile="grok"`` keeps the Lane 1 admit codes. ``profile="stamped"``
    checks the author-written part and refuses pipeline-owned keys.
    """
    prefix = _prefix(profile)
    invalid = f"{prefix}.handoff_invalid"
    if not isinstance(letter, dict):
        return invalid, "handoff"
    if handoff_json_size(letter) > HANDOFF_MAX_BYTES:
        return f"{prefix}.handoff_too_large", "handoff exceeds 16 KiB"
    if profile == "stamped":
        for key in _PIPELINE_OWNED:
            if key in letter:
                return invalid, key
        return _author_fields(letter, invalid)
    # Binding keys are optional at admit. Present keys keep today's shape check.
    # The envelope step stamps any key the author left out.
    if "house_cp" in letter:
        house_cp = letter.get("house_cp")
        if not isinstance(house_cp, str) or not _HOUSE_CP.fullmatch(house_cp):
            return invalid, "house_cp"
    if "messages_sha256" in letter:
        digest = letter.get("messages_sha256")
        if not isinstance(digest, str) or not _SHA64.fullmatch(digest):
            return invalid, "messages_sha256"
    if "as_of_message_index" in letter:
        index = letter.get("as_of_message_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 1:
            return invalid, "as_of_message_index"
    author = letter.get("author")
    if not isinstance(author, str) or not author.strip():
        return invalid, "author"
    mission = letter.get("mission")
    if (
        not isinstance(mission, str)
        or not mission
        or "\n" in mission
        or "\r" in mission
    ):
        return invalid, "mission"
    state = letter.get("state")
    if not isinstance(state, str):
        return invalid, "state"
    acts = letter.get("next_acts")
    if (
        not isinstance(acts, list)
        or not acts
        or any(not isinstance(item, str) for item in acts)
    ):
        return invalid, "next_acts"
    bearing = letter.get("load_bearing")
    if not isinstance(bearing, list) or any(
        not isinstance(item, str) for item in bearing
    ):
        return invalid, "load_bearing"
    if not bearing:
        reason = letter.get("load_bearing_reason")
        if not isinstance(reason, str) or not reason.strip():
            return invalid, "load_bearing"
    later = letter.get("later", None)
    if "later" in letter and not isinstance(later, (str, list)):
        return invalid, "later"
    return None


def fill_grok_binding(
    letter: dict[str, Any],
    *,
    messages: list[Any],
    messages_sha256: str,
    house: str,
    n: int,
) -> tuple[dict[str, Any], str]:
    """Fill absent grok binding keys from the pour. Does not mutate ``letter``.

    Returns the letter to store and ``handoff_binding_source`` (``pipeline`` when
    any key was filled, otherwise ``author``). Author-supplied keys are copied
    unchanged, including values that will later fail the binding check.
    """
    filled = dict(letter)
    filled_any = False
    if "house_cp" not in filled:
        filled["house_cp"] = f"house-cp:{house}:{n}"
        filled_any = True
    if "messages_sha256" not in filled:
        filled["messages_sha256"] = (
            bare_messages_sha(messages_sha256) or messages_sha256
        )
        filled_any = True
    if "as_of_message_index" not in filled:
        filled["as_of_message_index"] = len(messages)
        filled_any = True
    return filled, "pipeline" if filled_any else "author"


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


def _iso_z(now: str) -> str:
    text = str(now).strip()
    if text.endswith("+00:00"):
        return text[: -len("+00:00")] + "Z"
    if not text.endswith("Z"):
        return text + "Z"
    return text


def stamp_handoff(
    letter: dict[str, Any],
    *,
    session_id: str,
    messages_sha256: str,
    message_count: int,
    thread: str,
    transcript_id: str,
    surface: str,
    turn_count: int,
    now: str,
) -> dict[str, Any]:
    """Author letter plus the pipeline-owned stamp. Does not mutate ``letter``."""
    stamped = dict(letter)
    stamped["session_id"] = session_id
    stamped["messages_sha256"] = bare_messages_sha(messages_sha256) or messages_sha256
    stamped["as_of_message_index"] = message_count
    stamped["house"] = f"agent-bus:{thread}"
    stamped["transcript_id"] = transcript_id
    stamped["surface"] = surface
    stamped["stamped_at"] = _iso_z(now)
    stamped["turns_at_seal"] = turn_count
    return stamped


def handoff_binding_stamped(
    letter: object,
    *,
    messages: list[Any],
    messages_sha256: str,
    session_id: str,
) -> tuple[str, list[str]]:
    """Binding for a stamped letter against the sealed messages file."""
    if letter is None:
        return "missing", []
    if not isinstance(letter, dict):
        return "stale", ["shape"]
    reasons: list[str] = []
    if letter.get("messages_sha256") != messages_sha256:
        reasons.append("messages_sha256")
    if letter.get("as_of_message_index") != len(messages):
        reasons.append("as_of_message_index")
    if letter.get("session_id") != session_id:
        reasons.append("session_id")
    if reasons:
        return "stale", reasons
    return "bound", []


def handoff_lag_turns(letter: object) -> int | None:
    """``turns_at_seal - as_of_turn`` when both are ints. Bool is not an int here."""
    if not isinstance(letter, dict):
        return None
    seal_turns = letter.get("turns_at_seal")
    as_of = letter.get("as_of_turn")
    if isinstance(seal_turns, bool) or isinstance(as_of, bool):
        return None
    if isinstance(seal_turns, int) and isinstance(as_of, int):
        return seal_turns - as_of
    return None


def place_handoff(
    dumped: dict[str, Any],
    *,
    letter: object,
    prior: object,
    extend: bool,
    stamp: dict[str, Any] | None,
) -> str:
    """Write ``handoff`` onto a seal dump. Returns letter_status."""
    if isinstance(letter, dict) and stamp is not None:
        dumped["handoff"] = stamp_handoff(letter, **stamp)
        return "stamped"
    if extend and isinstance(prior, dict):
        dumped["handoff"] = dict(prior)
        return "carried"
    # No author letter: leave an envelope key the pour already carried (grok).
    # A cursor seal dump has no key, so the file stays without one.
    return "none"
