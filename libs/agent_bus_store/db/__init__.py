"""Agent-bus database layer - SQLite with WAL mode."""

from .connection import init_db
from .messages import get_messages, insert_message, list_threads, mark_read
from .threads import (
    ThreadHasReadTurns,
    create_thread,
    delete_thread,
    get_thread,
    get_thread_summary,
    get_thread_turns_asc,
    list_threads_v2,
    normalize_thread_id,
    rename_thread,
    set_thread_tags,
    update_thread,
)
from .threads_atomic import close_thread, create_thread_with_turn
from .turns import (
    TurnAlreadyAcknowledged,
    UnreadTurnsExist,
    delete_turn,
    get_turn_by_number,
    get_turns,
    insert_turn,
    mark_turn_read,
    update_turn,
    update_turn_status,
)

__all__ = [
    "ThreadHasReadTurns",
    "TurnAlreadyAcknowledged",
    "UnreadTurnsExist",
    "close_thread",
    "create_thread",
    "create_thread_with_turn",
    "delete_thread",
    "delete_turn",
    "get_messages",
    "get_thread",
    "get_thread_summary",
    "get_thread_turns_asc",
    "get_turn_by_number",
    "get_turns",
    "init_db",
    "insert_message",
    "insert_turn",
    "list_threads",
    "list_threads_v2",
    "mark_read",
    "mark_turn_read",
    "normalize_thread_id",
    "rename_thread",
    "set_thread_tags",
    "update_thread",
    "update_turn",
    "update_turn_status",
]
