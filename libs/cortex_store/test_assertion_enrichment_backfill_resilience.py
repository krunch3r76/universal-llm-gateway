"""Resilience: transient vs permanent dispatch errors and requeue."""

from __future__ import annotations

import pytest

from cortex_store.assertion_enrichment_backfill import (
    MAX_PERMANENT_ATTEMPTS,
    DispatchErrorClass,
    classify_dispatch_error,
    parse_dispatch_http,
    requeue_failed_outcomes,
)


def test_classify_503_transient() -> None:
    assert (
        classify_dispatch_error(status_code=503, body="", exc=None)
        == DispatchErrorClass.TRANSIENT
    )


def test_classify_token_counting_in_500_body() -> None:
    body = "Token counting failed for model qwen3-14b may not be loaded"
    assert (
        classify_dispatch_error(status_code=500, body=body, exc=None)
        == DispatchErrorClass.TRANSIENT
    )


def test_classify_permanent_422() -> None:
    assert (
        classify_dispatch_error(status_code=422, body="bad claim", exc=None)
        == DispatchErrorClass.PERMANENT
    )


def test_parse_dispatch_http_ok() -> None:
    r = parse_dispatch_http(200, "")
    assert r.ok and r.error_class is None


def test_requeue_legacy_transient_failed() -> None:
    state = {
        "outcomes": {
            "1102": {"status": "failed", "attempts": MAX_PERMANENT_ATTEMPTS},
            "9": {"status": "failed", "attempts": 1, "last_error": "HTTP 422 bad"},
        },
        "stats": {"failed": 2},
    }
    ids = requeue_failed_outcomes(state, transient_only=True)
    assert 1102 in ids
    assert 9 not in ids
    assert "1102" not in state["outcomes"]


def test_requeue_all_failed_retry_flag() -> None:
    state = {
        "outcomes": {
            "5": {
                "status": "failed",
                "attempts": 3,
                "last_error": "HTTP 422",
                "error_class": "permanent",
            }
        },
        "stats": {"failed": 1},
    }
    ids = requeue_failed_outcomes(state, transient_only=False)
    assert ids == [5]


def _load_runner_module():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    path = root / "scripts" / "assertion-enrichment-backfill.py"
    spec = importlib.util.spec_from_file_location("assertion_enrichment_backfill_runner", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
async def test_transient_does_not_consume_attempt_budget(monkeypatch) -> None:
    """503 then OK: permanent attempt count stays 0 until success."""
    runner = _load_runner_module()

    calls = {"n": 0}

    async def fake_dispatch(_client, row, *, kinds, redo):
        calls["n"] += 1
        aid = int(row["id"])
        if calls["n"] == 1:
            return aid, parse_dispatch_http(
                503,
                "Token counting failed for model qwen3-14b may not be loaded",
            )
        return aid, parse_dispatch_http(200, "ok")

    async def fast_outage(*_a, **_k):
        return None

    monkeypatch.setattr(runner, "_dispatch", fake_dispatch)
    monkeypatch.setattr(runner._OUTAGE, "handle_transient", fast_outage)

    state = {
        "cursor_id": 0,
        "outcomes": {},
        "stats": {"ok": 0, "failed": 0, "skipped": 0},
    }
    row = {"id": 42, "claim": "stargate loads qwen3-14b", "entity_id": "test:e"}
    db = runner.Path("/home/io/.cortex/cortex.db")
    if not db.is_file():
        pytest.skip("cortex.db unavailable")

    def always_verified(*_a, **_k):
        return True

    monkeypatch.setattr(runner, "field_populated", always_verified)

    await runner._run_rows(
        [row],
        kinds=("events",),
        redo=True,
        concurrency=1,
        delay_ms=0,
        db_path=db,
        state_path=runner.Path("/tmp/test-enrich-state.json"),
        log_path=runner.Path("/tmp/test-enrich.log"),
        state=state,
    )
    assert state["outcomes"]["42"]["status"] == "ok"
    assert state["outcomes"]["42"]["attempts"] == 1
