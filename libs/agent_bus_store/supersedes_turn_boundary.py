"""Translate wire ``supersedes_turn`` (turn_number) to DB row id at post boundary."""

from __future__ import annotations

from dataclasses import dataclass


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
    "resolve_supersedes_turn",
]
