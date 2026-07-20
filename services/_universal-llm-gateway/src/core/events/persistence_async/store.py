"""Non-blocking AsyncEventStore for SQLite-backed historical event persistence."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from universal_logging import get_logger

from . import store_sync

logger = get_logger(__name__)


class AsyncEventStore:
    """Non-blocking SQLite-based event storage for historical analysis."""

    def __init__(
        self,
        db_path: str = "/tmp/llm_gateway_events.db",
        max_events: int | None = 100000,
        auto_cleanup_days: int | None = 7,
    ):
        self.db_path = Path(db_path)
        self.max_events = max_events
        self.auto_cleanup_days = auto_cleanup_days

        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        self.executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="EventStore"
        )

        self._event_counter: int = 0
        self._cleanup_threshold: int = 1000

        store_sync.init_database(self.db_path)

        logger.info(f"📦 AsyncEventStore initialized: {db_path}")
        if max_events:
            logger.info(f"   Max events: {max_events}")
        if auto_cleanup_days:
            logger.info(f"   Auto-cleanup: {auto_cleanup_days} days")

    async def store_event(self, event: Any) -> bool:
        try:
            if not hasattr(event, "signal"):
                logger.error(
                    f"Invalid event format - missing 'signal' attribute: {type(event).__name__}"
                )
                return False

            event_type = event.signal
            timestamp = (
                event.timestamp
                if isinstance(event.timestamp, int | float)
                else time.time()
            )

            event_data = {
                "signal": event.signal,
                "payload": event.payload,
                "id": getattr(event, "id", None),
                "timestamp": event.timestamp,
            }

            event_json = json.dumps(event_data)

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                self.executor,
                store_sync.store_event_sync,
                self.db_path,
                timestamp,
                event_type,
                event_json,
            )

            self._event_counter += 1

            if (
                self.auto_cleanup_days
                and self._event_counter >= self._cleanup_threshold
            ):
                self._event_counter = 0
                asyncio.create_task(
                    self._auto_cleanup_async(),
                    name="event-store-cleanup",
                )

            if self.max_events:
                asyncio.create_task(self._enforce_max_events_async())

            return True

        except Exception as e:
            logger.error(f"Failed to store event {type(event).__name__}: {e}")
            return False

    async def query_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
        limit: int | None = 1000,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor,
                store_sync.query_events_sync,
                self.db_path,
                event_type,
                since,
                until,
                limit,
                offset,
            )

        except Exception as e:
            logger.error(f"Failed to query events: {e}")
            return []

    async def count_events(
        self,
        event_type: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> int:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor,
                store_sync.count_events_sync,
                self.db_path,
                event_type,
                since,
                until,
            )

        except Exception as e:
            logger.error(f"Failed to count events: {e}")
            return 0

    async def get_event_types(self) -> list[str]:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, store_sync.get_event_types_sync, self.db_path
            )

        except Exception as e:
            logger.error(f"Failed to get event types: {e}")
            return []

    async def cleanup_old_events(self, max_age_days: int) -> int:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor,
                store_sync.cleanup_old_events_sync,
                self.db_path,
                max_age_days,
            )

        except Exception as e:
            logger.error(f"Failed to cleanup old events: {e}")
            return 0

    async def _auto_cleanup_async(self):
        if not self.auto_cleanup_days:
            return

        try:
            await self.cleanup_old_events(self.auto_cleanup_days)
        except Exception as e:
            logger.debug(f"Auto-cleanup failed: {e}")

    async def _enforce_max_events_async(self):
        if not self.max_events:
            return

        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(
                self.executor, store_sync.get_event_count_sync, self.db_path
            )

            if count > self.max_events:
                to_delete = count - self.max_events
                await loop.run_in_executor(
                    self.executor,
                    store_sync.delete_oldest_events_sync,
                    self.db_path,
                    to_delete,
                )
                logger.debug(f"Deleted {to_delete} oldest events to enforce limit")

        except Exception as e:
            logger.debug(f"Failed to enforce max events: {e}")

    async def clear_all_events(self) -> int:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, store_sync.clear_all_events_sync, self.db_path
            )

        except Exception as e:
            logger.error(f"Failed to clear events: {e}")
            return 0

    async def get_stats(self) -> dict[str, Any]:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                self.executor, store_sync.get_stats_sync, self.db_path
            )

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {}

    async def close(self):
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)
        logger.info("📦 AsyncEventStore closed")
