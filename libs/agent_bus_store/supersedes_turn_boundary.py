"""Translate wire ``supersedes_turn`` (turn_number) to DB row id at post boundary."""

from __future__ import annotations

from dataclasses import dataclass

from .checkpoint_kind_detector import should_auto_derive_supersedes_turn
from .checkpoint_projection import CHECKPOINT_SUBJECT_SQL, is_checkpoint_subject


@dataclass(frozen=True, slots=True)
class SupersedesTurnResolved:
    """Resolved supersede target for insert_turn storage."""

    turn_number: int
    turn_id: int


class SupersedesTurnNotFoundError(Exception):
    """Raised when supersede target does not resolve in the given thread."""

    def __init__(
        self,
        *,
        thread: str,
        turn_number: int | None = None,
        turn_id: int | None = None,
    ) -> None:
        self.thread = thread
        self.turn_number = turn_number
        self.turn_id = turn_id
        if turn_number is not None:
            msg = f"supersedes_turn {turn_number} not found in thread {thread}"
        else:
            msg = f"supersedes_turn_id {turn_id} not found in thread {thread}"
        super().__init__(msg)

    def to_http_detail(self) -> dict[str, object]:
        detail: dict[str, object] = {
            "error": str(self),
            "reason": "supersedes_turn_not_found",
            "thread": self.thread,
        }
        if self.turn_number is not None:
            detail["turn_number"] = self.turn_number
        if self.turn_id is not None:
            detail["turn_id"] = self.turn_id
        return detail


def find_latest_checkpoint_turn_number(*, thread: str) -> int | None:
    """Return the highest turn_number whose subject is CHECKPOINT-shaped, if any."""
    from .db.connection import connect

    with connect() as conn:
        row = conn.execute(
            f"SELECT turn_number FROM turns "
            f"WHERE thread = ? AND {CHECKPOINT_SUBJECT_SQL} "
            f"ORDER BY turn_number DESC LIMIT 1",
            (thread,),
        ).fetchone()
    if row is None:
        return None
    return int(row["turn_number"])


def resolve_latest_checkpoint_supersedes(*, thread: str) -> SupersedesTurnResolved | None:
    """Atomically resolve the latest CHECKPOINT turn for auto-supersede sends."""
    from .db.connection import connect

    with connect() as conn:
        row = conn.execute(
            f"SELECT id, turn_number FROM turns "
            f"WHERE thread = ? AND {CHECKPOINT_SUBJECT_SQL} "
            f"ORDER BY turn_number DESC LIMIT 1",
            (thread,),
        ).fetchone()
    if row is None:
        return None
    return SupersedesTurnResolved(
        turn_number=int(row["turn_number"]),
        turn_id=int(row["id"]),
    )


def resolve_send_supersedes(
    *,
    thread: str,
    subject: str,
    thread_tags: list[str] | None,
    turn_number: int | None,
    turn_id_alias: int | None,
) -> SupersedesTurnResolved | None:
    """Resolve supersede target for send/reply, including gated auto-derive."""
    if should_auto_derive_supersedes_turn(
        subject=subject,
        thread_tags=thread_tags,
        turn_number=turn_number,
        turn_id_alias=turn_id_alias,
    ):
        return resolve_latest_checkpoint_supersedes(thread=thread)
    return resolve_supersedes_turn(
        thread=thread,
        turn_number=turn_number,
        turn_id_alias=turn_id_alias,
    )


def derive_supersedes_turn_for_send(
    *,
    thread: str,
    subject: str,
    thread_tags: list[str] | None,
    turn_number: int | None = None,
    turn_id_alias: int | None = None,
) -> int | None:
    """Auto-derive wire ``supersedes_turn`` for standing-root CHECKPOINT continue sends."""
    if not should_auto_derive_supersedes_turn(
        subject=subject,
        thread_tags=thread_tags,
        turn_number=turn_number,
        turn_id_alias=turn_id_alias,
    ):
        return turn_number
    return find_latest_checkpoint_turn_number(thread=thread)


def resolve_supersedes_turn(
    *,
    thread: str,
    turn_number: int | None = None,
    turn_id_alias: int | None = None,
) -> SupersedesTurnResolved | None:
    """Resolve wire turn_number (or deprecated row-id alias) to storage row id."""
    from .db.connection import connect

    if turn_number is None and turn_id_alias is None:
        return None

    with connect() as conn:
        if turn_number is not None:
            row = conn.execute(
                "SELECT id, turn_number FROM turns WHERE thread = ? AND turn_number = ?",
                (thread, turn_number),
            ).fetchone()
            if row is None:
                raise SupersedesTurnNotFoundError(thread=thread, turn_number=turn_number)
            return SupersedesTurnResolved(
                turn_number=int(row["turn_number"]),
                turn_id=int(row["id"]),
            )

        row = conn.execute(
            "SELECT id, turn_number FROM turns WHERE thread = ? AND id = ?",
            (thread, turn_id_alias),
        ).fetchone()
        if row is None:
            raise SupersedesTurnNotFoundError(thread=thread, turn_id=turn_id_alias)
        return SupersedesTurnResolved(
            turn_number=int(row["turn_number"]),
            turn_id=int(row["id"]),
        )


__all__ = [
    "SupersedesTurnNotFoundError",
    "SupersedesTurnResolved",
    "derive_supersedes_turn_for_send",
    "find_latest_checkpoint_turn_number",
    "resolve_latest_checkpoint_supersedes",
    "resolve_send_supersedes",
    "resolve_supersedes_turn",
]
