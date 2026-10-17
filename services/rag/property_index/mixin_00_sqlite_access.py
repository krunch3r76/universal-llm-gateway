"""Dedicated sqlite thread helpers for PropertyIndex (friction:39548)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from ._spec import Callable, sqlite3

_T = TypeVar("_T")


class _PropertyIndexSqliteAccess:
    """Single-thread sqlite executor; composed before mixin_01 lifecycle."""

    _conn: sqlite3.Connection | None
    _sqlite_executor: ThreadPoolExecutor | None

    def _close_conn_on_sqlite_thread(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _ensure_conn(self) -> sqlite3.Connection:
        assert self._conn is not None, "PropertyIndex not started"
        return self._conn

    def run_sqlite(self, fn: Callable[[], _T]) -> _T:
        assert self._sqlite_executor is not None, "PropertyIndex not started"
        return self._sqlite_executor.submit(fn).result()

    async def run_sqlite_async(self, fn: Callable[[], _T]) -> _T:
        assert self._sqlite_executor is not None, "PropertyIndex not started"
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._sqlite_executor, fn)

    def _with_conn(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        return self.run_sqlite(lambda: fn(self._ensure_conn()))

    async def _with_conn_async(self, fn: Callable[[sqlite3.Connection], _T]) -> _T:
        return await self.run_sqlite_async(lambda: fn(self._ensure_conn()))
