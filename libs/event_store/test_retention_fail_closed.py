"""Retention fails closed, never deletes before an operator pass, and open() never builds indexes."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from event_store.errors import EventStoreReadDeadlineError
from event_store.operations_trace import _event_store_floor
from event_store.retention_config import load_retention_settings
from event_store.schema import has_model_id_ts_index
from event_store.server import _retention_loop
from event_store.store import EventStore


def _write_cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str) -> None:
    cfg = tmp_path / "event-service.yaml"
    cfg.write_text(body, encoding="utf-8")
    monkeypatch.setenv("EVENT_SERVICE_CONFIG_PATH", str(cfg))
    monkeypatch.delenv("EVENTS_RETENTION_STARTUP", raising=False)


@pytest.mark.offline
@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("retention:\n  automatic: off\n", "off"),
        ("retention:\n  automatic: false\n", "off"),
        ("retention:\n  automatic: no\n", "off"),
        ("retention:\n  automatic: 1\n", "dry_run"),
        ("retention:\n  automatic: yes-please\n", "dry_run"),
        ("retention:\n  days: 2\n", "dry_run"),
        ("retention: [unclosed\n", "dry_run"),
        ("- just\n- a list\n", "dry_run"),
    ],
)
def test_automatic_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str, expected: str
) -> None:
    _write_cfg(tmp_path, monkeypatch, body)
    assert load_retention_settings().automatic == expected


@pytest.mark.offline
def test_missing_days_uses_cli_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(tmp_path, monkeypatch, "retention:\n  automatic: run\n")
    settings = load_retention_settings(cli_retention_days=9, cli_max_sessions=4)
    assert settings.retention_days == 9
    assert settings.max_sessions == 4


@pytest.mark.offline
def test_automatic_run_is_dry_until_operator_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_cfg(tmp_path, monkeypatch, "retention:\n  days: 1\n  automatic: run\n")

    async def _run(armed: threading.Event) -> int:
        store = EventStore(":memory:")
        store._retention_batch_sleep_s = 0
        await store.open()
        try:
            old = int(time.time() * 1000) - 3 * 86_400_000
            await store.insert_events(
                [
                    {
                        "signal": "old.row",
                        "role": "observation",
                        "scope": "global",
                        "ts_unix_ms": old,
                        "timestamp": "t",
                        "source": "test",
                        "payload": {},
                    }
                ]
            )
            try:
                await asyncio.wait_for(
                    _retention_loop(store, cli_retention_days=1, armed=armed),
                    timeout=1.0,
                )
            except TimeoutError:
                pass
            rows = await store.query(
                "SELECT COUNT(*) AS n FROM events WHERE signal = 'old.row'", ()
            )
            return int(rows[0]["n"])
        finally:
            await store.close()

    assert asyncio.run(_run(threading.Event())) == 1
    armed = threading.Event()
    armed.set()
    assert asyncio.run(_run(armed)) == 0


@pytest.mark.offline
def test_floor_degrades_on_read_deadline() -> None:
    async def _run() -> dict:
        store = EventStore(":memory:")
        await store.open()

        async def _slow(*_a: object, **_k: object) -> list:
            raise EventStoreReadDeadlineError("deadline")

        store.query = _slow  # type: ignore[method-assign]
        try:
            return await _event_store_floor(store)
        finally:
            await store.close()

    result = asyncio.run(_run())
    assert result["error"] == "floor_unavailable"
    assert result["event_count"] is None


@pytest.mark.offline
def test_open_verifies_but_does_not_build_model_id_ts_index(tmp_path: Path) -> None:
    async def _run() -> tuple[bool, bool, bool]:
        store = EventStore(tmp_path / "events.db")
        await store.open()
        try:
            assert store._db is not None
            before = has_model_id_ts_index(store._db)
            first = await store.build_model_id_ts_index()
            second = await store.build_model_id_ts_index()
            return before, first, second
        finally:
            await store.close()

    before, first, second = asyncio.run(_run())
    assert before is False
    assert first is True
    assert second is False
