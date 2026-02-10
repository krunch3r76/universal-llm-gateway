"""
Event Persistence - Non-blocking SQLite-based event storage for historical analysis.

Provides AsyncEventStore class for storing and querying events with:
- Automatic table creation and schema management
- Event serialization to JSON
- Time-based queries
- Type-based filtering
- Automatic cleanup and rotation policies
- Non-blocking async operations using thread pools
"""

import asyncio
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class AsyncEventStore:
    """
    Non-blocking SQLite-based event storage for historical analysis.

    Stores events in a SQLite database with automatic schema management,
    cleanup policies, and query capabilities using thread pools for non-blocking operations.

    Example:
        store = AsyncEventStore("/tmp/llm_gateway_events.db")

        # Store events (non-blocking)
        await store.store_event(ModelLoaded(...))

        # Query events (non-blocking)
        recent_events = await store.query_events(since=time.time() - 3600)  # Last hour
        model_events = await store.query_events(event_type="ModelLoaded")

        # Cleanup old events (non-blocking)
        await store.cleanup_old_events(max_age_days=7)
    """

    def __init__(
        self,
        db_path: str = "/tmp/llm_gateway_events.db",
        max_events: int | None = 100000,
        auto_cleanup_days: int | None = 7,
    ):
        """
        Initialize event store with SQLite database.

        Args:
            db_path: Path to SQLite database file
            max_events: Maximum number of events to keep (None for unlimited)
            auto_cleanup_days: Automatically delete events older than N days (None to disable)
        """
        self.db_path = Path(db_path)
        self.max_events = max_events
        self.auto_cleanup_days = auto_cleanup_days

        # Create directory if needed
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Thread pool for non-blocking database operations
        self.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="EventStore"
        )

        # Local counter for cleanup trigger (avoids DB query)
        self._event_counter: int = 0
        self._cleanup_threshold: int = 1000

        # Initialize database
        self._init_database()

        logger.info(f"📦 AsyncEventStore initialized: {db_path}")
        if max_events:
            logger.info(f"   Max events: {max_events}")
        if auto_cleanup_days:
            logger.info(f"   Auto-cleanup: {auto_cleanup_days} days")

    def _init_database(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    event_type TEXT NOT NULL,
                    event_data TEXT NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indices for common queries
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp
                ON events(timestamp DESC)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_event_type
                ON events(event_type)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON events(created_at DESC)
            """)

            conn.commit()

    async def store_event(self, event: Any) -> bool:
        """
        Store an event in the database (non-blocking).

        Args:
            event: Event object to store (UML Message structure)

        Returns:
            True if stored successfully, False otherwise
        """
        try:
            # Extract event data from UML Message structure
            if not hasattr(event, "signal"):
                logger.error(
                    f"Invalid event format - missing 'signal' attribute: {type(event).__name__}"
                )
                return False

            # UML Message structure: Event(signal, payload, timestamp, id)
            event_type = event.signal
            timestamp = (
                event.timestamp
                if isinstance(event.timestamp, int | float)
                else time.time()
            )

            # Store the full event structure including auto-injected fields
            event_data = {
                "signal": event.signal,
                "payload": event.payload,
                "id": getattr(event, "id", None),
                "timestamp": event.timestamp,
            }

            event_json = json.dumps(event_data)

            # Store in database using thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self.executor, self._store_event_sync, timestamp, event_type, event_json
            )

            # Increment local counter (avoids DB query for cleanup trigger)
            self._event_counter += 1

            # Auto-cleanup if threshold reached (non-blocking)
            if (
                self.auto_cleanup_days
                and self._event_counter >= self._cleanup_threshold
            ):
                self._event_counter = 0
                asyncio.create_task(
                    self._auto_cleanup_async(),
                    name="event-store-cleanup",
                )

            # Enforce max events limit (non-blocking)
            if self.max_events:
                asyncio.create_task(self._enforce_max_events_async())

            return True

        except Exception as e:
            logger.error(f"Failed to store event {type(event).__name__}: {e}")
            return False

    def _store_event_sync(
        self, timestamp: float, event_type: str, event_json: str
    ) -> None:
        """Synchronous database operation (runs in thread pool)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events (timestamp, event_type, event_data) VALUES (?, ?, ?)",
                (timestamp, event_type, event_json),
            )
            conn.commit()

    async def query_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int | None = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """
        Query events from the database (non-blocking).

        Args:
            event_type: Filter by event type name (e.g., "ModelLoaded")
            since: Filter events after this timestamp (Unix timestamp)
            until: Filter events before this timestamp (Unix timestamp)
            limit: Maximum number of events to return
            offset: Number of events to skip

        Returns:
            List of event dictionaries with metadata
        """
        try:
            # Run query in thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor,
                self._query_events_sync,
                event_type,
                since,
                until,
                limit,
                offset,
            )

        except Exception as e:
            logger.error(f"Failed to query events: {e}")
            return []

    def _query_events_sync(
        self,
        event_type: str | None,
        since: float | None,
        until: float | None,
        limit: int | None,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Synchronous query operation (runs in thread pool)"""
        query = "SELECT id, timestamp, event_type, event_data, created_at FROM events WHERE 1=1"
        params = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        if until:
            query += " AND timestamp <= ?"
            params.append(until)

        query += " ORDER BY timestamp DESC"

        if limit:
            query += " LIMIT ?"
            params.append(limit)

        if offset:
            query += " OFFSET ?"
            params.append(offset)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()

        # Convert to list of dicts
        events = []
        for row in rows:
            event_dict = dict(row)
            # Parse JSON event data
            event_dict["event_data"] = json.loads(event_dict["event_data"])
            events.append(event_dict)

        return events

    async def count_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> int:
        """
        Count events matching criteria (non-blocking).

        Args:
            event_type: Filter by event type name
            since: Filter events after this timestamp
            until: Filter events before this timestamp

        Returns:
            Number of matching events
        """
        try:
            # Run count in thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, self._count_events_sync, event_type, since, until
            )

        except Exception as e:
            logger.error(f"Failed to count events: {e}")
            return 0

    def _count_events_sync(
        self, event_type: str | None, since: float | None, until: float | None
    ) -> int:
        """Synchronous count operation (runs in thread pool)"""
        query = "SELECT COUNT(*) FROM events WHERE 1=1"
        params = []

        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        if since:
            query += " AND timestamp >= ?"
            params.append(since)

        if until:
            query += " AND timestamp <= ?"
            params.append(until)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(query, params)
            count = cursor.fetchone()[0]

        return count

    async def get_event_types(self) -> list[str]:
        """
        Get list of all event types in the database (non-blocking).

        Returns:
            List of event type names
        """
        try:
            # Run query in thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self.executor, self._get_event_types_sync)

        except Exception as e:
            logger.error(f"Failed to get event types: {e}")
            return []

    def _get_event_types_sync(self) -> list[str]:
        """Synchronous query operation (runs in thread pool)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT DISTINCT event_type FROM events ORDER BY event_type"
            )
            types = [row[0] for row in cursor.fetchall()]

        return types

    async def cleanup_old_events(self, max_age_days: int) -> int:
        """
        Delete events older than specified age (non-blocking).

        Args:
            max_age_days: Delete events older than this many days

        Returns:
            Number of events deleted
        """
        try:
            # Run cleanup in thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, self._cleanup_old_events_sync, max_age_days
            )

        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}")
            return 0

    def _cleanup_old_events_sync(self, max_age_days: int) -> int:
        """Synchronous cleanup operation (runs in thread pool)"""
        cutoff_time = time.time() - (max_age_days * 24 * 3600)

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM events WHERE timestamp < ?", (cutoff_time,)
            )
            deleted_count = cursor.rowcount
            conn.commit()

        if deleted_count > 0:
            logger.info(
                f"🗑️  Cleaned up {deleted_count} events older than {max_age_days} days"
            )

        return deleted_count

    async def _auto_cleanup_async(self):
        """Perform automatic cleanup if configured (non-blocking).

        OPTIMIZED: Uses local counter instead of DB query.
        Called only when threshold is reached (counter reset in store_event).
        """
        if not self.auto_cleanup_days:
            return

        # Just do the cleanup (threshold already checked via local counter)
        try:
            await self.cleanup_old_events(self.auto_cleanup_days)
        except Exception as e:
            logger.debug(f"Auto-cleanup failed: {e}")

    def _get_event_count_sync(self) -> int:
        """Synchronous count operation (runs in thread pool)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM events")
            count = cursor.fetchone()[0]
        return count

    async def _enforce_max_events_async(self):
        """Enforce maximum events limit by deleting oldest (non-blocking)"""
        if not self.max_events:
            return

        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(
                self.executor, self._get_event_count_sync
            )

            # Delete oldest if over limit
            if count > self.max_events:
                to_delete = count - self.max_events
                await loop.run_in_executor(
                    self.executor, self._delete_oldest_events_sync, to_delete
                )
                logger.debug(f"Deleted {to_delete} oldest events to enforce limit")

        except Exception as e:
            logger.debug(f"Failed to enforce max events: {e}")

    def _delete_oldest_events_sync(self, to_delete: int) -> None:
        """Synchronous delete operation (runs in thread pool)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"""
                DELETE FROM events WHERE id IN (
                    SELECT id FROM events ORDER BY timestamp ASC LIMIT {to_delete}
                )
            """)
            conn.commit()

    async def clear_all_events(self) -> int:
        """
        Delete all events from the database (non-blocking).

        Returns:
            Number of events deleted
        """
        try:
            # Run clear in thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, self._clear_all_events_sync
            )

        except Exception as e:
            logger.error(f"Failed to clear events: {e}")
            return 0

    def _clear_all_events_sync(self) -> int:
        """Synchronous clear operation (runs in thread pool)"""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM events")
            deleted_count = cursor.rowcount
            conn.commit()

        logger.info(f"🗑️  Cleared all {deleted_count} events from database")
        return deleted_count

    async def get_stats(self) -> dict[str, Any]:
        """
        Get database statistics (non-blocking).

        Returns:
            Dictionary with event statistics
        """
        try:
            # Run stats query in thread pool (non-blocking)
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self.executor, self._get_stats_sync)

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

    def _get_stats_sync(self) -> dict[str, Any]:
        """Synchronous stats operation (runs in thread pool)"""
        with sqlite3.connect(self.db_path) as conn:
            # Total count
            cursor = conn.execute("SELECT COUNT(*) FROM events")
            total_count = cursor.fetchone()[0]

            # Oldest and newest timestamps
            cursor = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM events")
            min_ts, max_ts = cursor.fetchone()

            # Event type counts
            cursor = conn.execute("""
                SELECT event_type, COUNT(*) as count
                FROM events
                GROUP BY event_type
                ORDER BY count DESC
            """)
            type_counts = {row[0]: row[1] for row in cursor.fetchall()}

            # Database file size
            db_size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0

        return {
            "total_events": total_count,
            "oldest_timestamp": min_ts,
            "newest_timestamp": max_ts,
            "time_span_seconds": (max_ts - min_ts) if min_ts and max_ts else 0,
            "event_type_counts": type_counts,
            "database_size_bytes": db_size_bytes,
            "database_size_mb": db_size_bytes / (1024 * 1024),
        }

    async def close(self):
        """Close database connections and thread pool (non-blocking)"""
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)
        logger.info("📦 AsyncEventStore closed")


class AsyncEventStoreSubscriber:
    """
    Subscriber that automatically stores events in AsyncEventStore.

    Integrates with EventBus to automatically persist all events.
    Works with UML Message structure.

    Example:
        event_bus = EventBus()
        event_store = AsyncEventStore("/tmp/events.db")

        # Subscribe to specific signals
        subscriber = AsyncEventStoreSubscriber(
            event_bus,
            event_store,
            event_signals=["ModelLoaded", "InferenceCompleted"]
        )

        # All published events matching these signals are now automatically stored
    """

    def __init__(
        self,
        event_bus,
        event_store: AsyncEventStore,
        event_signals: list[str] | None = None,
    ):
        """
        Initialize event store subscriber.

        Args:
            event_bus: EventBus to subscribe to
            event_store: AsyncEventStore to store events in
            event_signals: List of signal names (strings) to store
        """
        self.event_bus = event_bus
        self.event_store = event_store
        self.event_signals = event_signals

        # Subscribe to events by signal names
        if event_signals:
            for signal in event_signals:
                event_bus.subscribe_async(signal, self._handle_event)
                logger.debug(
                    f"AsyncEventStoreSubscriber: Subscribed to signal '{signal}'"
                )
        else:
            logger.warning(
                "AsyncEventStoreSubscriber: No event_signals specified, manual subscription required"
            )

    async def _handle_event(self, event: Any):
        """Handle event by storing it (non-blocking)"""
        await self.event_store.store_event(event)
