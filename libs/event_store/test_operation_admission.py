"""Handlers read only catalog-declared param keys."""

from __future__ import annotations

import pytest

from event_store.operation_catalog import get_operation
from event_store.operation_dispatch import _DISPATCH
from event_store.store import EventStore


class _Recording(dict):
    def __init__(self) -> None:
        super().__init__()
        self.seen: set[str] = set()

    def get(self, key: str, default: object = None) -> object:  # type: ignore[override]
        self.seen.add(key)
        return super().get(key, default)

    def __getitem__(self, key: str) -> object:
        self.seen.add(key)
        return super().__getitem__(key)


def _dummy(kind: str) -> object:
    if kind == "int":
        return 1
    if kind == "list":
        return []
    return "x"


@pytest.mark.asyncio
async def test_handlers_read_only_declared_keys() -> None:
    store = EventStore(":memory:")
    await store.open()
    try:
        for name, handler in _DISPATCH.items():
            op = get_operation(name)
            assert op is not None
            params = _Recording()
            for key, meta in op.params.items():
                if meta.get("required"):
                    params[key] = _dummy(str(meta.get("type") or "string"))
            try:
                await handler(params, store)
            except Exception:
                pass
            assert params.seen <= set(op.params), name
    finally:
        await store.close()
