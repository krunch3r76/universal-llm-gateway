"""spawn-endpoint-house-ledger module acceptance tests (G3 r6 AC1–AC5, AC12)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import cursor_bridge.spawn_endpoint_house_ledger as ledger
import pytest

pytestmark = pytest.mark.offline


def test_missing_kind_reads_as_wake(tmp_path: Path) -> None:
    path = ledger.ledger_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"agent_id": "a1", "ok": True, "op": "wake_tab"}) + "\n",
        encoding="utf-8",
    )
    assert ledger.last_successful_wake("a1", watch_dir=tmp_path) is not None


def test_line_at_4kb_rejected(tmp_path: Path) -> None:
    blob = "x" * 4096
    out = ledger.record_spawn(
        surface="cursor_sdk",
        thread="t1",
        purpose=blob,
        purpose_unstated=False,
        watch_dir=tmp_path,
    )
    assert out == {"ok": False, "error": "line_too_long"}
    assert not ledger.ledger_path(tmp_path).exists()


def test_partial_trailing_line_ignored(tmp_path: Path) -> None:
    path = ledger.ledger_path(tmp_path)
    path.write_bytes(b'{"kind":"wake","agent_id":"a","ok":true}')
    assert ledger.last_successful_wake("a", watch_dir=tmp_path) is None


def test_append_leading_newline_when_file_unterminated(tmp_path: Path) -> None:
    path = ledger.ledger_path(tmp_path)
    path.write_bytes(b'{"kind":"wake","agent_id":"a","ok":true}')
    ledger.record_tab_wake(
        gui_host=":8",
        agent_id="b",
        op="wake_tab",
        ok=True,
        watch_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert text.count("\n") >= 2
    assert not text.endswith('{"kind":"wake"')


def test_surface_enum_rejects_unknown(tmp_path: Path) -> None:
    out = ledger.record_spawn(
        surface="unknown",
        thread="t",
        purpose="p",
        purpose_unstated=False,
        watch_dir=tmp_path,
    )
    assert out["ok"] is False
    assert out["error"] == "surface_invalid"


def test_duplicate_row_id_rejected(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    first = ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=False,
        row_id=rid,
        watch_dir=tmp_path,
    )
    assert first["ok"] is True
    second = ledger.record_spawn(
        surface="cdp",
        thread="t2",
        purpose="p2",
        purpose_unstated=False,
        row_id=rid,
        watch_dir=tmp_path,
    )
    assert second == {"ok": False, "error": "duplicate_row_id"}


def test_row_id_is_32_hex_not_execution_id(tmp_path: Path) -> None:
    execution_id = str(uuid.uuid4())
    out = ledger.record_spawn(
        surface="cdp",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        execution_id=execution_id,
        watch_dir=tmp_path,
    )
    assert out["ok"] is True
    assert out["row_id"] != execution_id
    assert len(out["row_id"]) == 32


def test_terminal_source_beats_ledger_status(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    ledger.record_status_row(
        row_id=rid,
        status="live",
        source_status="STATUS| ok",
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(
        rid,
        watch_dir=tmp_path,
        terminal_source="CLOSEOUT complete",
    )
    assert proj["status"] == "done"
    assert proj["source_status"] == "CLOSEOUT complete"


def test_ledger_report_when_no_terminal(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    ledger.record_report_row(
        row_id=rid,
        status="parked",
        source_status="PARKED",
        thread="t",
        turn=3,
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(rid, watch_dir=tmp_path)
    assert proj["status"] == "parked"


def test_registry_parked_is_read_only(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(
        rid,
        watch_dir=tmp_path,
        registry_read=lambda: "PARKED",
        giw_read=lambda: None,
    )
    assert proj["status"] == "parked"


def test_status_map_closeout_and_markers() -> None:
    assert ledger.map_source_status("CLOSEOUT partial") == "parked"
    assert ledger.map_source_status("DONE| shipped") == "done"
    assert ledger.map_source_status("STATUS| alive") == "live"
    assert ledger.map_source_status("seat_closed_at=1") == "done"


def test_status_miss_returns_unknown(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    proj = ledger.project_row_status(rid, watch_dir=tmp_path)
    assert proj["status"] == "unknown"
    assert proj["source_status"] is None


def test_no_grace_constant() -> None:
    assert not any("grace" in name for name in dir(ledger))


def test_ingest_report_prefers_body_row_id(tmp_path: Path) -> None:
    rid_a = uuid.uuid4().hex
    rid_b = uuid.uuid4().hex
    for rid in (rid_a, rid_b):
        ledger.record_spawn(
            surface="cursor_sdk",
            thread=f"t-{rid[:4]}",
            purpose="p",
            purpose_unstated=True,
            row_id=rid,
            watch_dir=tmp_path,
        )
    body = f"ledger_row: {rid_b}\nrest"
    out = ledger.ingest_report_turn(body, "other", "t-x", turn=2, watch_dir=tmp_path)
    assert out["kind"] == "report"
    assert out["row_id"] == rid_b


def test_ingest_report_turn_from_caller(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "",
        "t",
        turn=7,
        watch_dir=tmp_path,
    )
    assert out["turn"] == 7


def test_ingest_report_turn_null_when_omitted(tmp_path: Path) -> None:
    rid = uuid.uuid4().hex
    ledger.record_spawn(
        surface="cursor_sdk",
        thread="t",
        purpose="p",
        purpose_unstated=True,
        row_id=rid,
        watch_dir=tmp_path,
    )
    out = ledger.ingest_report_turn(
        f"ledger_row: {rid}\n",
        "",
        "t",
        watch_dir=tmp_path,
    )
    assert out["turn"] is None
