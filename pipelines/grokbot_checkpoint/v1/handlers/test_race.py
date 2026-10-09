"""Two first seals of one new conversation, interleaved at the dedupe read."""

from __future__ import annotations

import asyncio
import threading

import pytest

from .seal import run_seal
from .test_contract import _URI, _boot, _envelope, _messages, _options, _step

pytestmark = pytest.mark.offline


def _count_rows() -> int:
    import cortex_store.db as cortex_db

    conn = cortex_db.cortex_conn()
    try:
        return int(conn.execute("SELECT COUNT(*) FROM session_journals").fetchone()[0])
    finally:
        conn.close()


def test_concurrent_first_seals_one_row(monkeypatch, tmp_path) -> None:
    _boot(monkeypatch, tmp_path)
    import cortex_store.routes.session_close as session_close

    barrier = threading.Barrier(2, timeout=10)
    real = session_close.try_idempotent_session_close
    seen = {"n": 0}
    gate = threading.Lock()

    def _interleave(body, ctx):
        found = real(body, ctx)
        with gate:
            seen["n"] += 1
            n = seen["n"]
        if n <= 2:
            barrier.wait()
        return found

    monkeypatch.setattr(session_close, "try_idempotent_session_close", _interleave)

    step = _step(_envelope(_messages(1)), _URI)
    options = _options("agent-race")
    results: list[dict] = []
    errors: list[BaseException] = []

    async def _one() -> None:
        class _Dispatch:
            async def __call__(self, tool: str, args: dict) -> dict:
                from cortex_store.dispatch_ops.ops_session_close import (
                    _op_session_close,
                )

                if tool != "session_close":
                    raise AssertionError(tool)
                return _op_session_close(**args)

        try:
            out = await run_seal(
                options, step, execution_id="e", dispatch=_Dispatch()
            )
            results.append(out)
        except BaseException as exc:
            errors.append(exc)

    def _thread() -> None:
        asyncio.run(_one())

    threads = [threading.Thread(target=_thread) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert errors == []
    assert _count_rows() == 1
    assert results[0]["session_id"] == results[1]["session_id"]
    assert results[0].get("refused") is None
    assert results[1].get("refused") is None
