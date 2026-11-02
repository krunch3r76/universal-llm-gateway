"""K115 paste-lane separation gate for role:root house threads."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status

from .db.connection import connect
from .db.threads import normalize_thread_id
from .enrollment_guard import normalize_tag_list
from .thread_classification import ROLE_ROOT_TAG

PASTE_LANE_TAG = "lane:paste"
PASTE_LANE_WRITE_TAGS: tuple[str, ...] = ("lane:paste",)
PASTE_LANE_REFUSED_CODE = "house_chat_turn_requires_paste_lane"
PASTE_LANE_REFUSED_SIGNAL = "agent_bus.route_control.paste_lane_refused"

HOUSE_CHARTER_SUBJECT_HEADS: frozenset[str] = frozenset(
    {
        "COMMISSION",
        "DECOMMISSION",
        "WAKE",
        "ACK",
        "RULING",
        "AMEND",
        "PARK",
        "CANCEL",
        "CHECKPOINT",
        "HOUSE-BIRTH",
        "CUTOVER",
        "RELEASE",
        "DECISION",
        "OBJECTIVE",
        "CLOSEOUT",
        "SEAT_REGISTRATION",
        "STANDING_BIND",
        "DIRECTIVE",
        "DISPOSITION",
    }
)

HOUSE_CHARTER_BODY_TYPE_LINES: frozenset[str] = frozenset(
    {
        "TYPE: ROUTE/CONTROL",
        "TYPE: SEAT_REGISTRATION",
        "TYPE: STANDING_BIND",
        "TYPE: DIRECTIVE",
        "TYPE: DISPOSITION",
        "TYPE: CLOSEOUT",
    }
)


def is_role_root_house(tags: list[str] | None) -> bool:
    """True when normalized tags include role:root."""
    return ROLE_ROOT_TAG in normalize_tag_list(tags)


def extract_house_turn_head(subject: str | None) -> str:
    """Extract uppercased subject head token for house-charter classification."""
    text = (subject or "").strip()
    if not text:
        return ""
    if text.lower().startswith("type:"):
        text = text[5:].lstrip()
    seg = text.split("|", 1)[0].strip()
    seg = seg.split(":", 1)[0].strip()
    if not seg:
        return ""
    return seg.split()[0].upper()


def _normalized_first_body_type_line(body: str | None) -> str:
    for line in (body or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.lower().startswith("type:"):
            return ""
        _, _, rest = stripped.partition(":")
        rest = rest.strip()
        if not rest:
            return ""
        kind_token = rest.split()[0].upper()
        return f"TYPE: {kind_token}"
    return ""


def is_house_charter_turn(
    *,
    subject: str | None,
    body: str | None,
    prior_turn: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    """Return (allowed_on_house, subject_head_or_label)."""
    head = extract_house_turn_head(subject)
    if head in HOUSE_CHARTER_SUBJECT_HEADS:
        return True, head
    body_type = _normalized_first_body_type_line(body)
    if body_type in HOUSE_CHARTER_BODY_TYPE_LINES:
        return True, head or body_type
    if head == "POINTER":
        prior_head = (
            extract_house_turn_head(str(prior_turn.get("subject") or ""))
            if prior_turn
            else ""
        )
        if prior_head != "POINTER":
            return True, "POINTER"
        return False, "POINTER"
    return False, head


def paste_lane_for(house_thread_id: str) -> str | None:
    """Newest active lane:paste side child by MAX(association_id)."""
    house_id = normalize_thread_id(house_thread_id)
    sql = """
        SELECT tla.thread_id
        FROM thread_lane_associations tla
        INNER JOIN (
            SELECT thread_id, MAX(id) AS max_id
            FROM thread_lane_associations
            GROUP BY thread_id
        ) latest
            ON tla.thread_id = latest.thread_id AND tla.id = latest.max_id
        INNER JOIN threads t
            ON t.id = tla.thread_id
        INNER JOIN thread_tags tt
            ON tt.thread_id = t.id
        WHERE tla.parent_thread_id = ?
          AND tla.lane_role = 'side'
          AND t.status != 'closed'
          AND tt.tag = ?
        ORDER BY tla.id DESC, CAST(tla.thread_id AS INTEGER) DESC, tla.thread_id DESC
        LIMIT 1
    """
    with connect() as conn:
        row = conn.execute(sql, (house_id, PASTE_LANE_TAG)).fetchone()
    return str(row["thread_id"]) if row is not None else None


def house_paste_lane_refusal_detail(
    *,
    house_thread_id: str,
    paste_lane_thread_id: str | None,
    subject_head: str,
) -> dict[str, Any]:
    """Structured HTTP 422 detail for K115 house paste-lane refusal."""
    head_label = subject_head or "untagged"
    if paste_lane_thread_id is not None:
        message = (
            f"Chat-shaped turn ({head_label}) refused on role:root house thread "
            f"{house_thread_id}; re-send to paste lane thread {paste_lane_thread_id}."
        )
        fix_hint = (
            f"Re-send with thread={paste_lane_thread_id!r} (active lane:paste child "
            f"of house {house_thread_id}) and post at most one POINTER| turn on "
            f"house {house_thread_id} per burst."
        )
    else:
        message = (
            f"Chat-shaped turn ({head_label}) refused on role:root house thread "
            f"{house_thread_id}; no active lane:paste child thread exists."
        )
        fix_hint = (
            f"Mint a paste-lane child first via POST /threads/send with "
            f"new_slug='<house-slug>-paste', parent_thread={house_thread_id!r}, "
            f"lane_role='side', tags=['lane:paste'] (do NOT include 'lane:liaison'), "
            f"then send chat-shaped turns to that child thread."
        )
    return {
        "code": PASTE_LANE_REFUSED_CODE,
        "house_thread_id": house_thread_id,
        "paste_lane_thread_id": paste_lane_thread_id,
        "subject_head": subject_head,
        "message": message,
        "fix_hint": fix_hint,
        "retryable": True,
        "source": "agent_bus_store.house_paste_lane_gate",
        "data": {
            "house_thread_id": house_thread_id,
            "paste_lane_thread_id": paste_lane_thread_id,
            "subject_head": subject_head,
            "fix_hint": fix_hint,
        },
    }


def assert_house_paste_lane_gate(
    *,
    thread_id: str,
    from_agent: str,
    subject: str | None,
    body: str | None,
    tags: list[str] | None,
    prior_turn: dict[str, Any] | None = None,
) -> None:
    """Refuse chat-shaped turns on role:root houses with HTTP 422 (K115)."""
    if not is_role_root_house(tags):
        return
    allowed, head = is_house_charter_turn(
        subject=subject,
        body=body,
        prior_turn=prior_turn,
    )
    if allowed:
        return
    paste_lane_id = paste_lane_for(thread_id)
    detail = house_paste_lane_refusal_detail(
        house_thread_id=thread_id,
        paste_lane_thread_id=paste_lane_id,
        subject_head=head,
    )
    from .events.publisher import emit

    emit(
        PASTE_LANE_REFUSED_SIGNAL,
        {
            "house_thread_id": thread_id,
            "paste_lane_thread_id": paste_lane_id,
            "from_agent": from_agent,
            "subject_head": head,
        },
    )
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=detail,
    )


__all__ = [
    "HOUSE_CHARTER_BODY_TYPE_LINES",
    "HOUSE_CHARTER_SUBJECT_HEADS",
    "PASTE_LANE_REFUSED_CODE",
    "PASTE_LANE_REFUSED_SIGNAL",
    "PASTE_LANE_TAG",
    "PASTE_LANE_WRITE_TAGS",
    "assert_house_paste_lane_gate",
    "extract_house_turn_head",
    "house_paste_lane_refusal_detail",
    "is_house_charter_turn",
    "is_role_root_house",
    "paste_lane_for",
]
