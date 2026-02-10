"""
Per-remote cursor tracking for HTTP polling.

INVARIANT: seq is monotonically increasing within a session
INVARIANT: seq=0 indicates remote restart (full snapshot required)

Persistence: Cursors are in-memory only. On Master restart:
1. Cursors reset to 0
2. Remotes detected as restarted (seq=0 on first poll)
3. Full snapshot requested via ?full=true

This is acceptable because Remotes also request full sync
when they detect Master reconnection.
"""

from dataclasses import dataclass


@dataclass
class RemoteCursor:
    """Tracks polling cursor for a single remote."""

    remote_id: str
    last_seen_seq: int = 0
    last_poll_timestamp_ms: int = 0
    consecutive_empty_polls: int = 0

    def should_accept(self, update_seq: int) -> bool:
        """
        Check if update should be accepted (not stale/duplicate).

        Returns:
            True if update should be processed
            False if out-of-order or duplicate
        """
        if update_seq == 0:
            # Remote restart - accept and reset cursor
            return True
        return update_seq > self.last_seen_seq

    def advance(self, update_seq: int, timestamp_ms: int) -> None:
        """Advance cursor after accepting update."""
        if update_seq == 0:
            # Remote restart - reset to 0
            self.last_seen_seq = 0
        else:
            self.last_seen_seq = update_seq
        self.last_poll_timestamp_ms = timestamp_ms
        self.consecutive_empty_polls = 0

    def record_empty_poll(self, timestamp_ms: int) -> None:
        """Record empty poll (no changes on remote)."""
        self.last_poll_timestamp_ms = timestamp_ms
        self.consecutive_empty_polls += 1


class CursorManager:
    """
    Manages cursors for all polling remotes.

    In-memory only - see module docstring for persistence semantics.
    """

    def __init__(self) -> None:
        self._cursors: dict[str, RemoteCursor] = {}

    def get_or_create(self, remote_id: str) -> RemoteCursor:
        """Get existing cursor or create new one."""
        if remote_id not in self._cursors:
            self._cursors[remote_id] = RemoteCursor(remote_id=remote_id)
        return self._cursors[remote_id]

    def remove(self, remote_id: str) -> None:
        """Remove cursor for remote."""
        self._cursors.pop(remote_id, None)

    def reset_all(self) -> None:
        """Reset all cursors (for testing or recovery)."""
        self._cursors.clear()
