"""Ledger rendering — verdict honesty, cadence gaps, auto-regeneration."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from unclaimed_property_hunter.ledger import (
    _LEDGER_REL,
    cadence_gaps,
    load_all_run_dicts,
    normalize_sidecar,
    regenerate_ledger,
    render_ledger_markdown,
    render_run_row,
)
from unclaimed_property_hunter.record import _RUNS_REL


def _patch_files_root(monkeypatch, tmp_path: Path) -> Path:
    runs = tmp_path / _RUNS_REL
    runs.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("unclaimed_property_hunter.record._FILES_ROOT", tmp_path)
    return runs


def _write_sidecar(runs_dir: Path, payload: dict) -> None:
    path = runs_dir / f"{payload['run_id']}.normalized.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _bulk_payload(
    *,
    run_id: str,
    utc_timestamp: str,
    search_executed: bool,
    hit_count: int | None = None,
    verdict: str | None = None,
    check_failed: bool = False,
    check_failure_reason: str | None = None,
    execution_block_reason: str | None = None,
) -> dict:
    data: dict = {
        "run_id": run_id,
        "utc_timestamp": utc_timestamp,
        "query": {"surname": "Mansubi"},
        "run_kind": "bulk_extract",
        "search_executed": search_executed,
        "raw_payload_uri": f"cortex://notes/system/unclaimed-property/runs/{run_id}.raw",
        "raw_sha256": "sha",
        "hits": [],
        "check_failed": check_failed,
        "check_failure_reason": check_failure_reason,
    }
    if hit_count is not None:
        data["hit_count"] = hit_count
    if verdict is not None:
        data["verdict"] = verdict
    if execution_block_reason is not None:
        data["execution_block_reason"] = execution_block_reason
    if search_executed and hit_count is None:
        data["hits"] = []
    return data


def test_not_executed_row_distinguishable_from_executed_zero(tmp_path, monkeypatch):
    """AC-2: NOT EXECUTED rows must not read like a completed zero-hit search."""
    runs_dir = _patch_files_root(monkeypatch, tmp_path)
    _write_sidecar(
        runs_dir,
        _bulk_payload(
            run_id="zero-1",
            utc_timestamp="2026-08-14T12:00:00Z",
            search_executed=True,
            hit_count=0,
            verdict="EXECUTED ZERO",
        ),
    )
    _write_sidecar(
        runs_dir,
        _bulk_payload(
            run_id="blocked-1",
            utc_timestamp="2026-08-14T11:00:00Z",
            search_executed=False,
            execution_block_reason="corpus_zero_rows",
            verdict="NOT EXECUTED",
        ),
    )
    rows = [render_run_row(r) for r in load_all_run_dicts()]
    not_executed = next(r for r in rows if "blocked-1" in r)
    executed_zero = next(r for r in rows if "zero-1" in r)
    assert "**NOT EXECUTED**" in not_executed
    assert "search did not execute" in not_executed
    assert "**EXECUTED ZERO**" in executed_zero
    assert "completed search — zero hits" in executed_zero
    assert "search did not execute" not in executed_zero


def test_missing_thursday_visible_as_gap_row(tmp_path, monkeypatch):
    """AC-3: a skipped Thursday appears as an explicit CADENCE GAP row."""
    runs_dir = _patch_files_root(monkeypatch, tmp_path)
    _write_sidecar(
        runs_dir,
        _bulk_payload(
            run_id="thu-a",
            utc_timestamp="2026-08-06T13:00:00Z",
            search_executed=True,
            hit_count=0,
            verdict="EXECUTED ZERO",
        ),
    )
    _write_sidecar(
        runs_dir,
        _bulk_payload(
            run_id="thu-b",
            utc_timestamp="2026-08-20T13:00:00Z",
            search_executed=True,
            hit_count=0,
            verdict="EXECUTED ZERO",
        ),
    )
    gaps = cadence_gaps(load_all_run_dicts(), today=date(2026, 8, 22))
    assert any(g.thursday.isoformat() == "2026-08-13" for g in gaps)
    md = render_ledger_markdown(load_all_run_dicts(), gaps)
    assert "**NO THURSDAY RUN**" in md
    assert "2026-08-13" in md


def test_new_run_appears_in_regenerated_ledger(tmp_path, monkeypatch):
    """AC-4: persisting a sidecar regenerates the ledger with that run."""
    runs_dir = _patch_files_root(monkeypatch, tmp_path)
    _write_sidecar(
        runs_dir,
        _bulk_payload(
            run_id="first-run",
            utc_timestamp="2026-08-06T13:00:00Z",
            search_executed=True,
            hit_count=0,
            verdict="EXECUTED ZERO",
        ),
    )
    regenerate_ledger()
    ledger_path = tmp_path / _LEDGER_REL
    assert "first-run" in ledger_path.read_text(encoding="utf-8")
    _write_sidecar(
        runs_dir,
        _bulk_payload(
            run_id="second-run",
            utc_timestamp="2026-08-13T13:00:00Z",
            search_executed=True,
            hit_count=1,
            verdict="EXECUTED HITS 1",
        ),
    )
    regenerate_ledger()
    text = ledger_path.read_text(encoding="utf-8")
    assert "first-run" in text
    assert "second-run" in text


def test_legacy_sidecars_without_verdict_do_not_crash(tmp_path, monkeypatch):
    """AC-5: pre-verdict sidecars load and render without invented outcomes."""
    runs_dir = _patch_files_root(monkeypatch, tmp_path)
    legacy_probe = {
        "run_id": "mansubi-20260814T095209Z",
        "utc_timestamp": "2026-08-14T09:52:09Z",
        "query": {"surname": "Mansubi"},
        "run_kind": "transport_probe",
        "search_executed": False,
        "hit_count": 0,
        "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/x.raw",
        "raw_sha256": "sha",
        "hits": [],
    }
    _write_sidecar(runs_dir, legacy_probe)
    normalized = normalize_sidecar(legacy_probe)
    assert normalized["verdict"] == "NOT EXECUTED"
    assert normalized["hit_count"] is None
    md = render_ledger_markdown(load_all_run_dicts(), [])
    assert "**NOT EXECUTED**" in md
    assert "**EXECUTED ZERO**" not in md


def test_five_existing_record_shapes_produce_valid_ledger(tmp_path, monkeypatch):
    """AC-5: mixed legacy + modern sidecars regenerate without error."""
    runs_dir = _patch_files_root(monkeypatch, tmp_path)
    fixtures = [
        {
            "run_id": "mansubi-20260814T095209Z",
            "utc_timestamp": "2026-08-14T09:52:09Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "transport_probe",
            "search_executed": False,
            "hit_count": 0,
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/a.raw",
            "raw_sha256": "1",
            "hits": [],
        },
        {
            "run_id": "mansubi-20260814T103252Z",
            "utc_timestamp": "2026-08-14T10:32:52Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "ingest_json",
            "search_executed": True,
            "hit_count": 2,
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/b.raw",
            "raw_sha256": "2",
            "hits": [{"property_id": "1"}, {"property_id": "2"}],
        },
        {
            "run_id": "mansubi-20260814T103310Z",
            "utc_timestamp": "2026-08-14T10:33:10Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "ingest_json",
            "search_executed": True,
            "hit_count": 2,
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/c.raw",
            "raw_sha256": "3",
            "hits": [],
        },
        {
            "run_id": "mansubi-20260814T103253Z",
            "utc_timestamp": "2026-08-14T10:32:53Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "ingest_html",
            "search_executed": True,
            "hit_count": 5,
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/d.raw",
            "raw_sha256": "4",
            "hits": [],
        },
        {
            "run_id": "mansubi-20260814T175521Z",
            "utc_timestamp": "2026-08-14T17:55:21Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "bulk_extract",
            "search_executed": True,
            "hit_count": 1,
            "hit_count_meaning": "completed_search",
            "execution_block_reason": None,
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/e.raw",
            "raw_sha256": "5",
            "hits": [],
            "check_failed": False,
        },
    ]
    for payload in fixtures:
        _write_sidecar(runs_dir, payload)
    uri = regenerate_ledger()
    text = (tmp_path / _LEDGER_REL).read_text(encoding="utf-8")
    assert uri.endswith("ledger.md")
    assert "# CA Unclaimed Property Hunt — Run Ledger" in text
    assert all(p["run_id"] in text for p in fixtures)
