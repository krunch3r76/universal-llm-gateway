"""R4 review follow-ups (D2): ledger spend_since, straddle, connect hygiene."""

from __future__ import annotations

import dataclasses
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from anthropic_credit.config import load_credit_config
from anthropic_credit.ledger import CreditLedger, LedgerUnavailable
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
            created_at="2026-09-19T12:00:00+00:00",
        )

    ledger.run_immediate(_seed)
    spend = ledger.run_immediate(
        lambda conn: CreditLedger.compute_spend_since_as_of(conn, as_of=_AS_OF)
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
    for entry in policy.status(now=t0)["entries"]:
        created = entry["created_at"]
        assert isinstance(created, str)
        assert created.endswith("+00:00")


def test_compute_spend_since_as_of_uses_created_at_index(tmp_path: Path) -> None:
    cfg = _make_cfg(tmp_path)
    ledger = CreditLedger(cfg.ledger_path, create_if_missing=False)

    def _plan(conn: sqlite3.Connection) -> list[str]:
        rows = conn.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT id FROM entries WHERE created_at >= ?
            """,
            (_AS_OF.isoformat(),),
        ).fetchall()
        return [str(row[3]) for row in rows]

    plan = ledger.run_immediate(_plan)
    plan_text = " ".join(plan).lower()
    assert "idx_entries_created_at" in plan_text or "using index" in plan_text


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
