from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from research_dialogue._types import SearchResult, SendResult, Snapshot
from research_dialogue.session import run_close, run_turn

pytestmark = pytest.mark.offline

_CONTRACT = """
<answer>ok</answer>
<ledger>F-a | x | high | rs-1 | f</ledger>
<next_queries>
llm_prompting | decay form
</next_queries>
<status> continue </status>
"""


def _snap(committed: str = "0") -> Snapshot:
    return Snapshot(
        as_of_utc=datetime(2026, 1, 1, tzinfo=UTC),
        healthy=True,
        committed_usd=Decimal(committed),
        open_reservations_usd=Decimal("0"),
        window_start="w",
        ledger_instance_id="i",
        latch_active=False,
    )


def _seed_state(session_dir: Path, *, turn_count: int = 1) -> None:
    state = {
        "committed_S0": "0",
        "window_start": "w",
        "ledger_instance_id": "i",
        "deliverable": "/dev/null",
        "minor_notes": str(session_dir / "minor.md"),
        "turn_count": turn_count,
        "history": [
            {"role": "user", "content": "u1"},
            {"role": "assistant", "content": _CONTRACT},
        ],
        "running_billed": "0.01",
        "last_ledger_text": "F-a | x | high | rs-1 | f",
        "pending_queries": [{"scope": "llm_prompting", "query": "decay form"}],
        "parse_fail_streak": 0,
        "stall_streak": 0,
    }
    session_dir.mkdir(parents=True, exist_ok=True)
    (session_dir / "minor.md").write_text("minor notes", encoding="utf-8")
    (session_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")


def test_turn2_search_receives_scope_and_query(tmp_path: Path) -> None:
    _seed_state(tmp_path)
    captured: list[tuple[str, str]] = []

    def search_fn(scope: str, query: str) -> SearchResult:
        captured.append((scope, query))
        return SearchResult(
            search_id="rs-test",
            scope=scope,
            query=query,
            weak_match=False,
            rerank_status="ok",
            top_ce=0.9,
            passages=[],
        )

    reads = [_snap(), _snap("0.02")]

    def read_fn() -> Snapshot:
        return reads.pop(0) if reads else _snap("0.02")

    def send_fn(body: dict) -> SendResult:
        return SendResult(
            ok=True,
            status_code=200,
            text=_CONTRACT,
            usage={
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            stop_reason="end_turn",
            message_id="m",
        )

    run_turn(tmp_path, read_fn=read_fn, send_fn=send_fn, search_fn=search_fn)
    assert captured == [("llm_prompting", "decay form")]


def test_turn_transport_error_writes_record(tmp_path: Path) -> None:
    _seed_state(tmp_path, turn_count=1)
    state_path = tmp_path / "state.json"
    state = json.loads(state_path.read_text())
    state["pending_queries"] = []
    state_path.write_text(json.dumps(state), encoding="utf-8")

    reads = [_snap(), _snap()]

    def read_fn() -> Snapshot:
        return reads.pop(0)

    def send_fn(body: dict) -> SendResult:
        raise httpx.ConnectError("connection refused")

    code, line = run_turn(tmp_path, read_fn=read_fn, send_fn=send_fn)
    assert code == 0
    assert line["stop_reason"] == "transport_error"
    turn_doc = json.loads((tmp_path / "turn-02.json").read_text())
    assert turn_doc["send_ok"] is False
    assert turn_doc["running_sum"]
    assert turn_doc["ledger_delta"]
    assert "post_open_usd" in turn_doc


def test_close_p4_records_turn(tmp_path: Path) -> None:
    _seed_state(tmp_path)
    reads = [_snap(), _snap("0.05")]

    def read_fn() -> Snapshot:
        return reads.pop(0) if reads else _snap("0.05")

    def send_fn(body: dict) -> SendResult:
        return SendResult(
            ok=True,
            status_code=200,
            text="closing note",
            usage={
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            stop_reason="end_turn",
            message_id="m",
        )

    run_close(tmp_path, stop_reason="turn_limit", read_fn=read_fn, send_fn=send_fn)
    doc = json.loads((tmp_path / "turn-02.json").read_text())
    assert doc["closing"] is True
    assert doc["running_sum"] != "0.01"
    note = (tmp_path / "design-note.md").read_text()
    assert "## Totals" in note
    assert "harness_minus_ledger_delta" in note
