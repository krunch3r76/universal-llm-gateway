"""Scope freshness repair debouncing for WatcherManager."""

from __future__ import annotations

import asyncio
from pathlib import Path

from services.rag.watcher_manager.protocols import IndexOutcome


class ScopeRepairMixin:
    def _schedule_scope_freshness_repair(self, scope: str) -> None:
        if self._scope_repair_runner is None:
            return
        self._pending_repair_scopes.add(scope)
        if self._repair_debounce_task is not None:
            self._repair_debounce_task.cancel()
        self._repair_debounce_task = asyncio.create_task(
            self._scope_repair_debounce_worker(),
            name="rag-scope-freshness-debounce",
        )

    async def _scope_repair_debounce_worker(self) -> None:
        try:
            await asyncio.sleep(self._scope_repair_debounce_s)
            scopes = set(self._pending_repair_scopes)
            if scopes and self._scope_repair_runner is not None:
                self._pending_repair_scopes.clear()
                await self._scope_repair_runner(scopes)
            else:
                self._pending_repair_scopes.clear()
        except asyncio.CancelledError:
            return
        finally:
            self._repair_debounce_task = None

    def _note_index_mutation(self, file_path: Path, result: IndexOutcome) -> None:
        if result.unchanged or result.indexed <= 0:
            return
        if self._scope_repair_runner is None or self._rag_config is None:
            return
        scope = self._rag_config.get_scope_for_path(
            str(file_path.expanduser().resolve())
        )
        self._schedule_scope_freshness_repair(scope)
