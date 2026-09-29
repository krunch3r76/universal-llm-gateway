"""422 gate for operator-lane ``TYPE: SEAT_REGISTRATION`` and ``TYPE: STANDING_BIND``."""

from __future__ import annotations

from typing import Any

from claude_bundles.operator_proxy_mission import MISSION_SKILL_SLUGS

from .db.connection import write_connect
from .db.threads import add_thread_tags, remove_thread_tags
from .enrollment_guard import normalize_tag_list
from .open_children import compute_open_children

_STANDING_MODEL_PREFIX = "standing-model:"
_STANDING_EFFORT_PREFIX = "standing-effort:"
_SEAT_REGISTRATION_PREFIX = "TYPE: SEAT_REGISTRATION"
_STANDING_BIND_LINE = "TYPE: STANDING_BIND"
_LANE_AUTO_TAG = "lane:cursor-auto"

def _first_non_empty_line(body: str) -> str | None:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _line_value(body: str, prefix: str) -> str | None:
    needle = prefix.lower()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith(needle):
            _, _, rest = stripped.partition(":")
            value = rest.strip()
            return value if value else None
    return None


def _standing_tags_from_thread(tags: list[str]) -> tuple[str | None, str | None]:
    model: str | None = None
    effort: str | None = None
    for tag in tags:
        if tag.startswith(_STANDING_MODEL_PREFIX):
            model = tag[len(_STANDING_MODEL_PREFIX) :]
        elif tag.startswith(_STANDING_EFFORT_PREFIX):
            effort = tag[len(_STANDING_EFFORT_PREFIX) :]
    return model, effort


def _is_operator_lane(tags: list[str]) -> bool:
    return _LANE_AUTO_TAG in tags


def _is_standing_bind(*, from_agent: str, body: str, tags: list[str]) -> bool:
    if from_agent != "web-anthropic" or not _is_operator_lane(tags):
        return False
    return _first_non_empty_line(body or "") == _STANDING_BIND_LINE


def _is_seat_registration(
    *,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str],
) -> bool:
    if from_agent == "cursor-auto":
        return False
    if from_agent != "web-anthropic" or not _is_operator_lane(tags):
        return False
    first = _first_non_empty_line(body or "")
    if first and first.startswith(_SEAT_REGISTRATION_PREFIX):
        return True
    return (subject or "").strip().startswith(_SEAT_REGISTRATION_PREFIX)


def _parse_skills_held(body: str) -> set[str] | None:
    raw = _line_value(body, "skills_held")
    if raw is None:
        return None
    return {part.strip() for part in raw.split(",") if part.strip()}


def _parse_open_children(body: str) -> set[str] | None:
    raw = _line_value(body, "open_children")
    if raw is None:
        return None
    if raw.casefold() == "none":
        return set()
    return {part.strip() for part in raw.split(",") if part.strip()}


def _has_maestro_fetch_decision(body: str) -> bool:
    for line in body.splitlines():
        text = line.strip()
        if "fetch-decision:" in text and "runbook:maestro-loop" in text:
            return True
    return False


def apply_standing_bind(
    *,
    thread_id: str,
    from_agent: str,
    body: str,
    tags: list[str],
) -> dict[str, Any] | None:
    """Persist standing model/effort tags from ``TYPE: STANDING_BIND``, or refuse."""
    if not _is_standing_bind(from_agent=from_agent, body=body, tags=tags):
        return None
    model = _line_value(body, "model")
    effort = _line_value(body, "effort")
    if not model or not effort:
        return {
            "reason": "standing_bind_incomplete",
            "fix_hint": "TYPE: STANDING_BIND requires non-empty model: and effort: lines.",
            "missing": [
                name
                for name, val in (("model", model), ("effort", effort))
                if not val
            ],
        }
    to_remove = [
        tag
        for tag in tags
        if tag.startswith(_STANDING_MODEL_PREFIX)
        or tag.startswith(_STANDING_EFFORT_PREFIX)
    ]
    new_pair = normalize_tag_list(
        [
            f"{_STANDING_MODEL_PREFIX}{model}",
            f"{_STANDING_EFFORT_PREFIX}{effort}",
        ]
    )
    with write_connect() as conn:
        if to_remove:
            remove_thread_tags(conn, thread_id, to_remove)
        add_thread_tags(conn, thread_id, new_pair)
    return None


def seat_registration_refusal(
    *,
    thread_id: str,
    from_agent: str,
    subject: str,
    body: str,
    tags: list[str],
) -> dict[str, Any] | None:
    """Return a 422 detail dict when registration fails checks; ``None`` to allow."""
    if not _is_seat_registration(
        from_agent=from_agent, subject=subject, body=body, tags=tags
    ):
        return None

    required_skills = set(MISSION_SKILL_SLUGS)
    held = _parse_skills_held(body)
    if held is None or held != required_skills:
        missing = sorted(required_skills - (held or set()))
        return {
            "reason": "seat_registration_skills_held",
            "fix_hint": (
                "skills_held must equal MISSION_SKILL_SLUGS: "
                f"{','.join(MISSION_SKILL_SLUGS)}"
            ),
            "missing": missing,
        }

    if not _has_maestro_fetch_decision(body):
        return {
            "reason": "seat_registration_fetch_decision",
            "fix_hint": "add a line: fetch-decision: runbook:maestro-loop",
            "missing": ["fetch-decision: runbook:maestro-loop"],
        }

    declared_open = _parse_open_children(body)
    if declared_open is None:
        computed = set(compute_open_children(thread_id))
        omitted = sorted(computed, key=int)
        ids_text = ", ".join(omitted) if omitted else "(none)"
        return {
            "reason": "seat_registration_open_children",
            "fix_hint": (
                f"open_children omitted {ids_text}. GET /threads/<parent>/open-children "
                "and list every returned id."
            ),
            "missing": omitted,
        }
    computed = set(compute_open_children(thread_id))
    if not computed.issubset(declared_open):
        omitted = sorted(computed - declared_open, key=int)
        ids_text = ", ".join(omitted)
        return {
            "reason": "seat_registration_open_children",
            "fix_hint": (
                f"open_children omitted {ids_text}. GET /threads/<parent>/open-children "
                "and list every returned id."
            ),
            "missing": omitted,
        }

    standing_model, standing_effort = _standing_tags_from_thread(tags)
    if not standing_model or not standing_effort:
        return {
            "reason": "seat_registration_standing_tags",
            "fix_hint": (
                "send TYPE: STANDING_BIND with model: and effort: lines, "
                "then repeat SEAT_REGISTRATION echoing those values."
            ),
            "missing": [
                name
                for name, val in (
                    ("standing-model", standing_model),
                    ("standing-effort", standing_effort),
                )
                if not val
            ],
        }

    reg_model = _line_value(body, "model")
    reg_effort = _line_value(body, "effort")
    if (
        reg_model is None
        or reg_effort is None
        or reg_model.casefold() != standing_model.casefold()
        or reg_effort.casefold() != standing_effort.casefold()
    ):
        return {
            "reason": "seat_registration_standing_echo",
            "fix_hint": (
                "send TYPE: STANDING_BIND with model: and effort: lines, "
                "then repeat SEAT_REGISTRATION echoing those values."
            ),
            "missing": ["model", "effort"],
        }

    return None


__all__ = ["apply_standing_bind", "seat_registration_refusal"]
