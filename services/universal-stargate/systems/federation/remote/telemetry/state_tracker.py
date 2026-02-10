"""
Edge-side telemetry state tracker for delta computation.

Runs on Remote nodes to compute deltas before sending to Master.

INVARIANT: ∀ update: delta computed at edge, not master
INVARIANT: Only changes trigger telemetry responses
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


# Tracked fields for delta computation
TRACKED_FIELDS = frozenset(
    [
        "loaded_models",
        "busy_models",
        "available_models",
        "active_requests",
        "vram_free_mb",
        "ram_free_mb",
    ]
)


@dataclass
class TelemetryDelta:
    """Represents changes between telemetry snapshots."""

    changes: dict[str, Any]
    sequence_number: int
    critical_events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        """Check if delta has any changes or events."""
        return len(self.changes) > 0 or len(self.critical_events) > 0

    @property
    def is_empty(self) -> bool:
        """Check if delta is empty (no changes, no events)."""
        return not self.has_changes


class TelemetryStateTracker:
    """
    Tracks telemetry state and computes deltas for edge-first architecture.

    Remote-side only. Master receives pre-computed deltas.
    """

    def __init__(self, node_id: str):
        """
        Initialize state tracker.

        Args:
            node_id: Remote node identifier
        """
        self._node_id = node_id
        self._previous_state: dict[str, Any] = {}
        self._current_state: dict[str, Any] = {}
        self._sequence_number = 0
        self._critical_events: list[dict[str, Any]] = []

        # Accumulate deltas since last successful delivery
        self._accumulated_delta: dict[str, Any] = {}
        self._accumulated_seq: int = 0  # Sequence when accumulator was last populated
        self._last_poll_seq: int = (
            0  # Sequence number on last poll (for delivery detection)
        )

    def update(self, state: dict[str, Any]) -> None:
        """
        Update current state (does not compute delta yet).

        Args:
            state: New telemetry state snapshot
        """
        self._current_state = state.copy()

    def add_critical_event(self, event_type: str, data: dict[str, Any]) -> None:
        """
        Add critical event to be included in next delta.

        Critical events are ALWAYS sent, even if delta is empty.

        Args:
            event_type: Event type (e.g., "MODEL_LOADED", "MODEL_UNLOADED")
            data: Event data
        """
        self._critical_events.append(
            {
                "event": event_type,
                "timestamp": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
                **data,
            }
        )
        logger.debug(f"Critical event queued: {event_type}")

    def get_delta(self) -> dict[str, Any]:
        """
        Get accumulated delta with auto-clearing on successful delivery.

        Clears accumulator if Master polls twice at same sequence (proof of delivery).
        This works for both HTTP polling and WebSocket without explicit acknowledgment.

        Returns:
            Delta dict with accumulated changes + sequence number.
            Includes critical events if any.
            Empty dict with only sequence_number if no changes (caller returns 204).
        """
        # If Master polled again at same sequence, previous delta was delivered
        # successfully.
        if self._last_poll_seq == self._sequence_number and self._accumulated_delta:
            logger.info(
                f"✅ Master polled again at seq {self._sequence_number}, "
                f"clearing delivered accumulator"
            )
            self._accumulated_delta = {}

        # Record this poll's sequence for next poll comparison
        self._last_poll_seq = self._sequence_number

        # Compute changes since last update
        current_delta = self._compute_delta()

        # If state changed, accumulate the changes and increment sequence
        if current_delta:
            self._sequence_number += 1

            # Merge into accumulated delta (later changes override earlier ones)
            self._accumulated_delta.update(current_delta)
            self._accumulated_seq = self._sequence_number

            # Update previous state for next comparison
            self._previous_state = self._current_state.copy()

            logger.debug(
                f"Accumulated delta updated (seq {self._sequence_number}): "
                f"changed fields: {list(current_delta.keys())}"
            )

        # Check if we have any accumulated changes or critical events to return
        has_accumulated = len(self._accumulated_delta) > 0
        has_critical_events = len(self._critical_events) > 0

        if not has_accumulated and not has_critical_events:
            # Nothing to report
            return {"sequence_number": self._sequence_number}

        # Build response with accumulated delta
        delta = self._accumulated_delta.copy()
        delta["sequence_number"] = self._sequence_number

        # Include critical events if any
        if self._critical_events:
            delta["critical_events"] = self._critical_events.copy()
            # Clear critical events after including (they're not retriable)
            self._critical_events.clear()

        logger.info(
            f"📤 Returning accumulated delta (seq {self._sequence_number}): "
            f"fields={list(delta.keys())}"
        )

        return delta

    def get_full_snapshot(self) -> dict[str, Any]:
        """
        Get full current state snapshot (for reconnect/sync).

        Returns:
            Complete current state with sequence number
        """
        snapshot = self._current_state.copy()
        snapshot["sequence_number"] = self._sequence_number

        # Merge previous state for any missing fields
        for key, value in self._previous_state.items():
            if key not in snapshot:
                snapshot[key] = value

        return snapshot

    def _compute_delta(self) -> dict[str, Any]:
        """
        Compute delta between previous and current state.

        Returns:
            Dict with only changed fields (may be empty)
        """
        delta = {}

        for key in TRACKED_FIELDS:
            prev_value = self._previous_state.get(key)
            curr_value = self._current_state.get(key)

            if prev_value != curr_value:
                # Check if this is a list field (models)
                if key in ("loaded_models", "busy_models", "available_models"):
                    delta[key] = self._compute_list_delta(
                        key,
                        prev_value or [],
                        curr_value or [],
                    )
                else:
                    # Scalar field - include new value
                    delta[key] = curr_value

        return delta

    def _compute_list_delta(
        self,
        field_name: str,
        prev_list: list,
        curr_list: list,
    ) -> dict[str, list] | list:
        """
        Compute delta for list fields (loaded_models, busy_models).

        Returns added/removed format if small delta, otherwise full list.

        Args:
            field_name: Field name for logging
            prev_list: Previous list
            curr_list: Current list

        Returns:
            {"added": [...], "removed": [...]} if small delta
            [...] if large delta (snapshot)
        """
        prev_set = set(prev_list)
        curr_set = set(curr_list)

        added = sorted(curr_set - prev_set)
        removed = sorted(prev_set - curr_set)

        # Use added/removed format if delta is smaller than full list
        # Threshold: use delta format if (added + removed) < (curr_list * 0.5)
        delta_size = len(added) + len(removed)
        full_size = len(curr_list)

        if delta_size > 0 and (full_size == 0 or delta_size < full_size * 0.5):
            return {"added": added, "removed": removed}
        else:
            # Full list format (no delta benefit or empty)
            return list(curr_list)

    @property
    def node_id(self) -> str:
        """Node identifier."""
        return self._node_id

    @property
    def timestamp(self) -> str:
        """Current timestamp in ISO 8601 format with Z suffix."""
        return datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z"
