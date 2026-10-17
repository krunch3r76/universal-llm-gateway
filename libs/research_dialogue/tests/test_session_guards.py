from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from research_dialogue._types import SendResult, Snapshot
from research_dialogue.session import TURN_LIMIT, run_close, run_turn

pytestmark = pytest.mark.offline


def _seed(session_dir: Path, **extra: object) -> None:
    state = {
        "committed_S0": "0",
        "window_start": "w",
        "ledger_instance_id": "i",
        "deliverable": "/dev/null",
        "minor_notes": str(session_dir / "minor.md"),
        "turn_count": 1,
        "history": [],
        "running_billed": "0",
        "parse_fail_streak": 0,
        "stall_streak": 0,
        **extra,
    }
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "minor.md").write_text("m", encoding="utf-8")
    (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def _snap() -> Snapshot:
    return Snapshot(
        as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal("0"),
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
        raw={"healthy": True, "ledger_path": "/tmp/x.sqlite"},
    )


def test_run_turn_refuses_when_stop_reason_set(tmp_path: Path) -> None:
    _seed(tmp_path, stop_reason="stall")
    code, out = run_turn(tmp_path, read_fn=_snap)
    assert code == 3
    assert out.get("send_skipped")


def test_run_turn_refuses_at_turn_limit(tmp_path: Path) -> None:
    _seed(tmp_path, turn_count=TURN_LIMIT)
    code, _ = run_turn(tmp_path, read_fn=_snap)
    assert code == 3


def test_run_close_refuses_second_closing(tmp_path: Path) -> None:
    _seed(tmp_path, closing_recorded=True)
    code, out = run_close(tmp_path, stop_reason="stall", no_p4=True)
    assert code == 3
    assert out.get("send_skipped")


def test_turn_json_records_billed_breakdown(tmp_path: Path) -> None:
    _seed(
        tmp_path,
        turn_count=1,
        history=[
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": "<answer>a</answer><ledger>L</ledger><next_queries></next_queries><status>continue</status>"},
        ],
        last_ledger_text="L",
        pending_queries=[],
    )
    reads = [_snap(), _snap()]

    def read_fn() -> Snapshot:
        return reads.pop(0)

    def send_fn(body: dict) -> SendResult:
        return SendResult(
            True,
            200,
            "<answer>a</answer><ledger>L</ledger><next_queries></next_queries><status>continue</status>",
            {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            "end_turn",
            "m",
        )

    run_turn(tmp_path, read_fn=read_fn, send_fn=send_fn)
    final = json.loads((tmp_path / "turn-02.json").read_text())
    assert final["send_ok"] is True
    assert "billed_usd" in final
    assert "billed_missing_fields" in final
    assert "billed_unknown_fields" in final
