"""Corpus provenance — source tokens, url honesty, ledger visibility."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from unclaimed_property_hunter.bulk_extract import fingerprint_existing_zip
from unclaimed_property_hunter.transport import BULK_ZIP_URL
from unclaimed_property_hunter.extract_pipeline import run_extract
from unclaimed_property_hunter.ledger import (
    _LEDGER_REL,
    load_all_run_dicts,
    regenerate_ledger,
    render_run_row,
)
from unclaimed_property_hunter.record import _RUNS_REL
from unclaimed_property_hunter.result_surface import public_run_dict


def _csv_zip(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for row in rows:
        buf.write(",".join(row) + "\n")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("All_Records.csv", buf.getvalue())
    return path


def test_local_disk_fingerprint_url_does_not_claim_state_get(tmp_path: Path):
    """AC-2: local-disk zip fingerprint must not use the state GET url."""
    zip_path = _csv_zip(
        tmp_path / "local.zip",
        ["OWNER_NAME", "PROPERTY_ID", "HOLDER_NAME"],
        [["SMITH", "1", "ACME"]],
    )
    fp = fingerprint_existing_zip(zip_path)
    assert fp.corpus_source == "local_disk"
    assert fp.url != BULK_ZIP_URL
    assert fp.url.startswith("file://")
    assert "local.zip" in fp.url


def _patch_files_root(monkeypatch, tmp_path: Path) -> Path:
    runs = tmp_path / _RUNS_REL
    runs.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("unclaimed_property_hunter.record._FILES_ROOT", tmp_path)
    return runs


def _write_sidecar(runs_dir: Path, payload: dict) -> None:
    path = runs_dir / f"{payload['run_id']}.normalized.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_ledger_executed_hits_row_shows_corpus_source(tmp_path, monkeypatch):
    """AC-3: EXECUTED HITS rows must show corpus provenance without opening JSON."""
    runs_dir = _patch_files_root(monkeypatch, tmp_path)
    _write_sidecar(
        runs_dir,
        {
            "run_id": "hits-with-source",
            "utc_timestamp": "2026-08-14T12:00:00Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "bulk_extract",
            "search_executed": True,
            "hit_count": 3,
            "verdict": "EXECUTED HITS 3",
            "corpus_source": "state_download",
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/hits-with-source.raw",
            "raw_sha256": "sha",
            "hits": [],
        },
    )
    row = render_run_row(load_all_run_dicts()[0])
    assert "**EXECUTED HITS 3**" in row
    assert "corpus: state download" in row
    assert "corpus: unestablished" not in row


def test_five_existing_records_render_unestablished_provenance(tmp_path, monkeypatch):
    """AC-4: legacy sidecars including EXECUTED HITS 25 must not read as state-sourced."""
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
            "verdict": "EXECUTED HITS 2",
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
            "verdict": "EXECUTED HITS 2",
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/c.raw",
            "raw_sha256": "3",
            "hits": [],
        },
        {
            "run_id": "mansubi-20260814T103253Z",
            "utc_timestamp": "2026-08-14T10:32:53Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "bulk_extract",
            "search_executed": True,
            "hit_count": 25,
            "verdict": "EXECUTED HITS 25",
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/d.raw",
            "raw_sha256": "4",
            "hits": [],
            "notes": "bulk_extract scanned=93270023 matched=25 zip=/tmp/ca-upd/00_All_Records.zip",
        },
        {
            "run_id": "mansubi-20260814T175521Z",
            "utc_timestamp": "2026-08-14T17:55:21Z",
            "query": {"surname": "Mansubi"},
            "run_kind": "bulk_extract",
            "search_executed": True,
            "hit_count": 1,
            "verdict": "EXECUTED HITS 1",
            "hit_count_meaning": "completed_search",
            "execution_block_reason": None,
            "raw_payload_uri": "cortex://notes/system/unclaimed-property/runs/e.raw",
            "raw_sha256": "5",
            "hits": [],
            "check_failed": False,
            "corpus_fingerprint": {
                "url": "https://claimit.ca.gov/upd-property-records/00_All_Records.zip",
                "last_modified": "",
                "etag": "",
                "content_length": 273,
                "zip_sha256": "696ce63383017d42b5589e2538605a48b4b4235f126a80ff6a3c89f833dcfedf",
                "rows_scanned": 1,
            },
        },
    ]
    for payload in fixtures:
        _write_sidecar(runs_dir, payload)
    regenerate_ledger()
    text = (tmp_path / _LEDGER_REL).read_text(encoding="utf-8")
    hits_25_row = next(
        line for line in text.splitlines() if "mansubi-20260814T103253Z" in line
    )
    hits_1_row = next(
        line for line in text.splitlines() if "mansubi-20260814T175521Z" in line
    )
    assert "corpus: unestablished" in hits_25_row
    assert "corpus: state download" not in hits_25_row
    assert "corpus: unestablished" in hits_1_row
    assert "corpus: state download" not in hits_1_row


def test_run_extract_local_disk_sets_corpus_source(tmp_path, monkeypatch):
    """New local-disk extracts carry corpus_source=local_disk in the sidecar projection."""
    zip_path = _csv_zip(
        tmp_path / "local.zip",
        ["OWNER_NAME", "PROPERTY_ID", "HOLDER_NAME"],
        [["MANSUBI FRED", "99", "ACME"]],
    )
    runs_dir = tmp_path / "notes/system/unclaimed-property/runs"
    runs_dir.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("unclaimed_property_hunter.record._FILES_ROOT", tmp_path)
    monkeypatch.setattr(
        "unclaimed_property_hunter.ledger.regenerate_ledger", lambda: "cortex://ledger"
    )
    monkeypatch.setattr(
        "unclaimed_property_hunter.record.persist_run",
        lambda _record: {"run_entity": {"id": "x"}},
    )

    record = run_extract(
        surname="Mansubi",
        also=[],
        zip_path=zip_path,
        download=False,
        notify=False,
    )
    payload = public_run_dict(record)
    assert payload["corpus_source"] == "local_disk"
    assert payload["corpus_fingerprint"]["corpus_source"] == "local_disk"
    assert payload["corpus_fingerprint"]["url"].startswith("file://")
