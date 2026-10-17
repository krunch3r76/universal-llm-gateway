"""R4 review follow-ups (D2): ledger spend_since, straddle, connect hygiene."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger import (
    OPEN_RESERVE_BEFORE_ASOF_SQL,
    SPEND_SINCE_AS_OF_SQL,
    TTL_EXPIRE_CANDIDATES_SQL,
    CreditLedger,
    LedgerUnavailable,
)
from anthropic_credit.ledger_init import init_ledger
from anthropic_credit.policy import Admit, CreditPolicy
from anthropic_credit.test_ledger_policy import _body_1_usd_on_sonnet, _make_cfg

pytestmark = pytest.mark.offline

_AS_OF = datetime(2026, 9, 20, 0, 0, 0, tzinfo=UTC)


def _insert_row(
    conn: sqlite3.Connection,
    *,
    kind: str,
    request_id: str,
    usd: str,
    created_at: str,
) -> None:
    conn.execute(
        """
        INSERT INTO entries (
            kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
        ) VALUES (?, ?, 'anthropic/claude-sonnet-5', ?, ?, '2026-10-03T10:00:00+00:00', NULL, NULL, 0)
        """,
        (kind, request_id, usd, created_at),
    )


def test_compute_spend_since_as_of_sql_excludes_pre_as_of_rows(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)

    def _seed(conn: sqlite3.Connection) -> None:
        _insert_row(
            conn,
            kind="commit",
            request_id="old-only",
            usd="5.000000",
            created_at="2026-09-10T12:00:00+00:00",
        )
        _insert_row(
            conn,
            kind="commit",
            request_id="new-only",
            usd="1.000000",
            created_at="2026-09-25T12:00:00+00:00",
        )

    ledger.run_immediate(_seed)
    spend = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(conn, as_of=_AS_OF)
    )
    assert spend == Decimal("1")


def test_compute_spend_since_as_of_straddle_reserve_before_commit_after(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)

    def _seed(conn: sqlite3.Connection) -> None:
        _insert_row(
            conn,
            kind="reserve",
            request_id="straddle",
            usd="1.000000",
            created_at="2026-09-19T12:00:00+00:00",
        )
        _insert_row(
            conn,
            kind="commit",
            request_id="straddle",
            usd="1.000000",
            created_at="2026-09-25T12:00:00+00:00",
        )

    ledger.run_immediate(_seed)
    spend = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(conn, as_of=_AS_OF)
    )
    assert spend == Decimal("1")


def test_connect_closes_connection_on_schema_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import MagicMock

    ledger_path = tmp_path / "bad_schema.sqlite"
    cfg = dataclasses.replace(load_credit_config(), ledger_path=ledger_path)
    init_ledger(cfg)
    with sqlite3.connect(str(ledger_path)) as conn:
        conn.execute("UPDATE meta SET value = '1' WHERE key = 'schema_version'")

    mock_conn = MagicMock()
    mock_conn.row_factory = sqlite3.Row
    _execute_calls: list[str] = []

    def _execute(sql: str, *_params: object) -> MagicMock:
        _execute_calls.append(sql)
        result = MagicMock()
        if "schema_version" in sql:
            result.fetchone.return_value = ("1",)
        elif "instance_id" in sql:
            result.fetchone.return_value = ("test-instance",)
        else:
            result.fetchone.return_value = None
        return result

    mock_conn.execute.side_effect = _execute

    def _fake_connect(*_args: object, **_kwargs: object) -> MagicMock:
        return mock_conn

    monkeypatch.setattr(sqlite3, "connect", _fake_connect)
    ledger = CreditLedger(ledger_path, create_if_missing=False)
    with pytest.raises(LedgerUnavailable, match="schema_mismatch"):
        ledger._connect()
    mock_conn.close.assert_called_once()
    assert not any("CREATE INDEX" in sql for sql in _execute_calls)


def test_compute_spend_since_as_of_open_reserve_before_as_of(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)

    def _seed(conn: sqlite3.Connection) -> None:
        _insert_row(
            conn,
            kind="reserve",
            request_id="open-old",
            usd="2.000000",
            created_at="2026-09-19T23:50:00+00:00",
        )

    ledger.run_immediate(_seed)
    spend = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(
            conn, as_of=_AS_OF, reservation_ttl_minutes=cfg.reservation_ttl_minutes
        )
    )
    assert spend == Decimal("2")


def test_compute_spend_since_as_of_straddle_commit_not_double_counted(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)

    def _seed(conn: sqlite3.Connection) -> None:
        _insert_row(
            conn,
            kind="reserve",
            request_id="straddle-open",
            usd="1.000000",
            created_at="2026-09-19T12:00:00+00:00",
        )
        _insert_row(
            conn,
            kind="commit",
            request_id="straddle-open",
            usd="1.000000",
            created_at="2026-09-25T12:00:00+00:00",
        )

    ledger.run_immediate(_seed)
    spend = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(conn, as_of=_AS_OF)
    )
    assert spend == Decimal("1")


def test_ledger_writes_created_at_utc_plus00_suffix(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "ts-fmt",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    policy.commit(
        "ts-fmt",
        {
            "input_tokens": 10,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 5,
        },
        now=t0,
    )
    assert isinstance(
        policy.admit(
            "ts-release",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    policy.release("ts-release", now=t0)
    assert isinstance(
        policy.admit(
            "ts-ttl",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            now=t0,
        ),
        Admit,
    )
    policy.latch("credit_balance_too_low", now=t0)
    for entry in policy.status(now=t0)["entries"]:
        created = entry["created_at"]
        assert isinstance(created, str)
        assert created.endswith("+00:00")
    ttl_at = t0 + timedelta(minutes=16)
    policy.status(now=ttl_at)
    ttl_commit = next(
        e
        for e in policy.status(now=ttl_at)["entries"]
        if e["request_id"] == "ts-ttl" and e.get("tag") == "ttl_expired"
    )
    assert str(ttl_commit["created_at"]).endswith("+00:00")


def test_compute_spend_since_as_of_uses_created_at_index(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)
    as_of_iso = _AS_OF.isoformat()

    def _seed(conn: sqlite3.Connection) -> None:
        for idx in range(2000):
            _insert_row(
                conn,
                kind="commit",
                request_id=f"explain-old-{idx}",
                usd="0.010000",
                created_at=f"2026-09-{10 + (idx % 9):02d}T12:00:00+00:00",
            )
        for idx in range(50):
            _insert_row(
                conn,
                kind="commit",
                request_id=f"explain-new-{idx}",
                usd="0.010000",
                created_at=f"2026-09-25T{(idx % 24):02d}:00:00+00:00",
            )

    ledger.run_immediate(_seed)
    conn = sqlite3.connect(str(cfg.ledger_path))
    try:
        conn.execute("ANALYZE")
        rows = conn.execute(
            f"EXPLAIN QUERY PLAN {SPEND_SINCE_AS_OF_SQL}",
            (as_of_iso,),
        ).fetchall()
        plan_text = " ".join(str(row[3]) for row in rows).lower()
    finally:
        conn.close()
    assert "idx_entries_created_at" in plan_text


def test_compute_spend_open_reserve_query_uses_created_at_index(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)
    as_of_iso = _AS_OF.isoformat()
    lower = (_AS_OF - timedelta(minutes=cfg.reservation_ttl_minutes)).isoformat()

    def _plan(conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            f"EXPLAIN QUERY PLAN {OPEN_RESERVE_BEFORE_ASOF_SQL}",
            (lower, as_of_iso),
        ).fetchall()
        return [str(row[3]) for row in rows]

    plan = ledger.run_immediate(_plan)
    assert "idx_entries_created_at" in " ".join(plan).lower()


def test_ttl_expire_candidates_query_uses_created_at_index(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)
    now = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    expire_before = (now - timedelta(minutes=cfg.reservation_ttl_minutes)).isoformat()
    lower = "2026-10-01T00:00:00+00:00"

    def _plan(conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            f"EXPLAIN QUERY PLAN {TTL_EXPIRE_CANDIDATES_SQL}",
            (lower, expire_before),
        ).fetchall()
        return [str(row[3]) for row in rows]

    plan = ledger.run_immediate(_plan)
    assert "idx_entries_created_at" in " ".join(plan).lower()


def test_ttl_sweep_watermark_bounds_second_pass(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)
    t0 = datetime(2026, 10, 17, 10, 0, 0, tzinfo=UTC)
    t1 = t0 + timedelta(minutes=20)

    def _seed(conn: sqlite3.Connection) -> None:
        _insert_row(
            conn,
            kind="reserve",
            request_id="stale-a",
            usd="1.000000",
            created_at="2026-10-17T09:30:00+00:00",
        )
        _insert_row(
            conn,
            kind="reserve",
            request_id="stale-b",
            usd="2.000000",
            created_at="2026-10-17T09:44:00+00:00",
        )

    ledger.run_immediate(_seed)
    first = policy.status(now=t0)["entries"]
    ttl_first = [e for e in first if e.get("tag") == "ttl_expired"]
    assert len(ttl_first) == 2
    watermark_after_first = ledger.run_immediate(
        lambda conn: CreditLedger._meta_get(conn, "ttl_swept_before")
    )
    assert watermark_after_first is not None
    second = policy.status(now=t1)["entries"]
    ttl_second = [e for e in second if e.get("tag") == "ttl_expired"]
    assert len(ttl_second) == 2


def test_orphan_ttl_commit_stays_in_previous_window(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)
    t_now = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    from anthropic_credit.policy import meter_window

    window_start, reset_at = meter_window(t_now, cfg)

    def _seed(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO entries (
                kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
            ) VALUES ('reserve', 'orphan-prev', 'anthropic/claude-sonnet-5', '3.000000',
                '2026-10-02T10:00:00+00:00', '2026-10-02T10:00:00+00:00', NULL, NULL, 0)
            """
        )

    ledger.run_immediate(_seed)
    snap_before = ledger.run_immediate(
        lambda conn: CreditLedger.compute_window_snapshot(
            conn,
            window_start=window_start,
            reset_at=reset_at,
            as_of=None,
            now=t_now,
            reservation_ttl_minutes=cfg.reservation_ttl_minutes,
        )
    )
    policy.status(now=t_now)
    snap_after = ledger.run_immediate(
        lambda conn: CreditLedger.compute_window_snapshot(
            conn,
            window_start=window_start,
            reset_at=reset_at,
            as_of=None,
            now=t_now,
            reservation_ttl_minutes=cfg.reservation_ttl_minutes,
        )
    )
    assert snap_after.committed_usd == snap_before.committed_usd


def test_orphan_reserve_previous_window_expired_once(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)
    t_prev = datetime(2026, 10, 2, 10, 0, 0, tzinfo=UTC)
    t_now = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)

    def _seed(conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            INSERT INTO entries (
                kind, request_id, model, usd, created_at, window_start, tag, caller_tag, mcp_class
            ) VALUES ('reserve', 'orphan-prev', 'anthropic/claude-sonnet-5', '3.000000',
                '2026-10-02T10:00:00+00:00', '2026-10-02T10:00:00+00:00', NULL, NULL, 0)
            """
        )

    ledger.run_immediate(_seed)
    policy.status(now=t_now)
    spend_once = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(
            conn,
            as_of=t_prev,
            reservation_ttl_minutes=cfg.reservation_ttl_minutes,
        )
    )
    spend_twice = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(
            conn,
            as_of=t_prev,
            reservation_ttl_minutes=cfg.reservation_ttl_minutes,
        )
    )
    entries = policy.status(now=t_now)["entries"]
    ttl_rows = [
        e
        for e in entries
        if e["request_id"] == "orphan-prev" and e["kind"] == "commit"
    ]
    assert len(ttl_rows) == 1
    assert ttl_rows[0].get("tag") == "ttl_expired"
    assert spend_once == Decimal("3")
    assert spend_twice == spend_once


def test_admit_refuses_future_available_balance_as_of(tmp_path: Path) -> None:
    future = datetime(2099, 1, 1, 0, 0, 0, tzinfo=UTC)
    base = load_credit_config()
    cfg = _make_cfg(
        tmp_path,
        available_balance=dataclasses.replace(
            base.available_balance,
            as_of=future.isoformat(),
        ),
    )
    policy = CreditPolicy(cfg)
    now = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    from anthropic_credit.policy import Refuse

    result = policy.admit(
        "future-asof",
        "anthropic/claude-sonnet-5",
        _body_1_usd_on_sonnet(),
        token_count=0,
        now=now,
    )
    assert isinstance(result, Refuse)
    assert result.code == "anthropic_credit_gate_unavailable"
    assert result.http_status == 503


def test_gate_extract_credit_tag_refuses_ttl_expired_string() -> None:
    from services.universal_cloud_proxy.adapters.anthropic_credit_gate import (
        extract_credit_tag,
    )

    assert extract_credit_tag(extra_headers={"x-ulg-credit-tag": "ttl_expired"}) is None
    assert extract_credit_tag(extra_headers={"x-ulg-credit-tag": "pipeline-x"}) == (
        "pipeline-x"
    )


def test_caller_tag_on_reserve_commit_and_ttl_expired_refused(
    tmp_path: Path,
) -> None:
    cfg = _make_cfg(tmp_path)
    policy = CreditPolicy(cfg)
    t0 = datetime(2026, 10, 17, 15, 0, 0, tzinfo=UTC)
    assert isinstance(
        policy.admit(
            "tagged-req",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            tag="my-caller",
            now=t0,
        ),
        Admit,
    )
    policy.commit(
        "tagged-req",
        {
            "input_tokens": 100,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 50,
        },
        now=t0,
    )
    entries = policy.status(now=t0)["entries"]
    reserve = next(e for e in entries if e["kind"] == "reserve")
    commit = next(e for e in entries if e["kind"] == "commit" and e["request_id"] == "tagged-req")
    assert reserve["caller_tag"] == "my-caller"
    assert commit["caller_tag"] == "my-caller"

    assert isinstance(
        policy.admit(
            "ttl-tag-refused",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            tag="ttl_expired",
            now=t0,
        ),
        Admit,
    )
    reserve2 = next(
        e for e in policy.status(now=t0)["entries"] if e["request_id"] == "ttl-tag-refused"
    )
    assert reserve2["caller_tag"] is None

    assert isinstance(
        policy.admit(
            "ttl-caller-tag",
            "anthropic/claude-sonnet-5",
            _body_1_usd_on_sonnet(),
            token_count=0,
            tag="pipeline-ttl",
            now=t0,
        ),
        Admit,
    )
    ttl_at = t0 + timedelta(minutes=16)
    policy.status(now=ttl_at)
    ttl_commit = next(
        e
        for e in policy.status(now=ttl_at)["entries"]
        if e["request_id"] == "ttl-caller-tag"
        and e["kind"] == "commit"
        and e.get("tag") == "ttl_expired"
    )
    assert ttl_commit["caller_tag"] == "pipeline-ttl"
