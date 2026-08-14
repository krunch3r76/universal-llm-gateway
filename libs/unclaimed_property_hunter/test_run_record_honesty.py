"""Run-record honesty — zero-row corpus, check_failed persist, roster_empty."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from unclaimed_property_hunter.bulk_extract import (
    HeaderDriftError,
    filter_zip_for_surnames,
)
from unclaimed_property_hunter.cli import _cmd_scheduled_extract
from unclaimed_property_hunter.extract_pipeline import (
    persist_check_failed_extract,
    run_extract,
    run_extract_or_persist_failure,
)
from unclaimed_property_hunter.hit_notify import (
    CHECK_FAILED_PAGE_THRESHOLD,
    decide_check_failed_notification,
    decide_roster_empty_notification,
)
from unclaimed_property_hunter.models import CorpusFingerprint, RunRecord
from unclaimed_property_hunter.record import consecutive_check_failed_runs
from unclaimed_property_hunter.result_surface import public_run_dict


def _csv_zip(path: Path, header: list[str], rows: list[list[str]]) -> Path:
    buf = io.StringIO()
    buf.write(",".join(header) + "\n")
    for row in rows:
        buf.write(",".join(row) + "\n")
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("All_Records.csv", buf.getvalue())
    return path


def _fingerprint() -> CorpusFingerprint:
    return CorpusFingerprint(
        url="https://claimit.ca.gov/upd-property-records/00_All_Records.zip",
        last_modified="",
        etag="",
        content_length=100,
        zip_sha256="abc123",
    )


def _patch_persist(monkeypatch, tmp_path: Path) -> list[Path]:
    saved: list[Path] = []
    runs_dir = tmp_path / "notes/system/unclaimed-property/runs"
    runs_dir.mkdir(parents=True)
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(tmp_path))
    monkeypatch.setattr("unclaimed_property_hunter.record._FILES_ROOT", tmp_path)

    def fake_write_bytes(rel: Path, data: bytes) -> tuple[str, str]:
        dest = runs_dir / rel.name
        dest.write_bytes(data)
        return f"cortex://{rel.as_posix()}", "sha"

    def fake_write_raw(record: RunRecord, raw_bytes: bytes) -> RunRecord:
        norm = runs_dir / f"{record.run_id}.normalized.json"
        norm.write_bytes(json.dumps(record.to_json_dict(), indent=2).encode())
        saved.append(norm)
        return RunRecord(
            run_id=record.run_id,
            utc_timestamp=record.utc_timestamp,
            query=record.query,
            run_kind=record.run_kind,
            search_executed=record.search_executed,
            raw_payload_uri=f"cortex://notes/system/unclaimed-property/runs/{record.run_id}.raw",
            raw_sha256="sha",
            hits=record.hits,
            notes=record.notes,
            corpus_fingerprint=record.corpus_fingerprint,
            notify_outcome=record.notify_outcome,
            check_failed=record.check_failed,
            check_failure_reason=record.check_failure_reason,
        )

    for target in (
        "unclaimed_property_hunter.record._write_bytes",
        "unclaimed_property_hunter.record.write_raw_and_normalized",
        "unclaimed_property_hunter.record.persist_run",
        "unclaimed_property_hunter.extract_pipeline.write_raw_and_normalized",
        "unclaimed_property_hunter.extract_pipeline.persist_run",
        "unclaimed_property_hunter.cli.write_raw_and_normalized",
        "unclaimed_property_hunter.cli.persist_run",
    ):
        if target.endswith("_write_bytes"):
            monkeypatch.setattr(target, fake_write_bytes)
        elif target.endswith("write_raw_and_normalized"):
            monkeypatch.setattr(target, fake_write_raw)
        else:
            monkeypatch.setattr(
                target, lambda record: {"run_entity": {"id": record.run_id}}
            )
    return saved


def test_zero_scanned_rows_cannot_serialize_executed_zero(tmp_path, monkeypatch):
    """Header-only CSV must not produce EXECUTED ZERO or hit_count=0."""
    zip_path = _csv_zip(
        tmp_path / "empty.csv.zip",
        ["OWNER_NAME", "PROPERTY_ID", "HOLDER_NAME"],
        [],
    )
    rows, scanned = filter_zip_for_surnames(zip_path, ["Testsubject"])
    assert scanned == 0
    assert rows == []

    saved = _patch_persist(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "unclaimed_property_hunter.extract_pipeline.fingerprint_existing_zip",
        lambda _p: _fingerprint(),
    )
    record = run_extract(
        surname="Testsubject",
        also=[],
        zip_path=zip_path,
        download=False,
        notify=False,
    )
    payload = public_run_dict(record)
    assert payload["search_executed"] is False
    assert payload["hit_count"] is None
    assert payload["hit_count"] != 0
    assert payload["verdict"] == "NOT EXECUTED"
    assert payload["execution_block_reason"] == "corpus_zero_rows"
    assert saved


def test_scheduled_extract_failure_persists_check_failed(tmp_path, monkeypatch):
    """Raised extract errors persist check_failed=True with machine-readable reason."""
    saved = _patch_persist(monkeypatch, tmp_path)
    zip_path = tmp_path / "missing.zip"
    monkeypatch.setattr(
        "unclaimed_property_hunter.extract_pipeline.run_extract",
        lambda **_kw: (_ for _ in ()).throw(ValueError("simulated download failure")),
    )
    record = run_extract_or_persist_failure(
        surname="Testsubject",
        also=[],
        zip_path=zip_path,
        download=True,
        notify=False,
    )
    assert record.check_failed is True
    assert record.check_failure_reason.startswith("extract_error:ValueError:")
    assert record.search_executed is False
    payload = public_run_dict(record)
    assert payload["check_failed"] is True
    assert saved


def test_three_consecutive_check_failures_produce_page_decision(
    tmp_path, monkeypatch
):
    """Three persisted check_failed runs reach threshold and produce a page decision."""
    saved = _patch_persist(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "unclaimed_property_hunter.extract_pipeline.notify_infrastructure_sync",
        lambda _record, decision: {
            "decision_reason": decision.reason,
            "pages": [{"status": "sent"}],
        },
    )
    times = iter(
        [
            "2026-08-14T12:00:00Z",
            "2026-08-14T12:00:01Z",
            "2026-08-14T12:00:02Z",
        ]
    )
    monkeypatch.setattr(
        "unclaimed_property_hunter.extract_pipeline.utc_now",
        lambda: next(times),
    )
    for i in range(CHECK_FAILED_PAGE_THRESHOLD):
        persist_check_failed_extract(
            surname="Testsubject",
            also=[],
            failure_reason=f"extract_error:RuntimeError:fail-{i}",
            notify=True,
        )
    assert len(saved) == CHECK_FAILED_PAGE_THRESHOLD
    streak = consecutive_check_failed_runs("Testsubject")
    assert streak == CHECK_FAILED_PAGE_THRESHOLD
    decision = decide_check_failed_notification(
        failure_reason="extract_error:RuntimeError:fail-2",
        consecutive_check_failed=streak,
    )
    assert decision.reason == f"check_failed_streak={CHECK_FAILED_PAGE_THRESHOLD}"


def test_missing_roster_terminates_roster_empty_exit_zero(tmp_path, monkeypatch):
    """Missing roster file notifies roster_empty once and exits 0."""
    _patch_persist(monkeypatch, tmp_path)
    notified: list[str] = []

    def fake_notify(_record, decision):
        notified.append(decision.reason)
        return {"decision_reason": decision.reason, "pages": [{"status": "sent"}]}

    monkeypatch.setattr(
        "unclaimed_property_hunter.cli.notify_infrastructure_sync", fake_notify
    )
    missing = tmp_path / "no-roster.yaml"

    class _Args:
        config = str(missing)

    assert _cmd_scheduled_extract(_Args()) == 0
    assert notified == ["roster_empty"]
    decision = decide_roster_empty_notification()
    assert decision.reason == "roster_empty"


def test_empty_roster_terminates_roster_empty_exit_zero(tmp_path, monkeypatch):
    """Empty subjects roster notifies roster_empty once and exits 0."""
    _patch_persist(monkeypatch, tmp_path)
    roster_path = tmp_path / "empty-roster.yaml"
    roster_path.write_text(
        "subjects: []\nzip_cache: /tmp/unused.zip\n",
        encoding="utf-8",
    )
    notified: list[str] = []

    def fake_notify(_record, decision):
        notified.append(decision.reason)
        return {"decision_reason": decision.reason, "pages": [{"status": "sent"}]}

    monkeypatch.setattr(
        "unclaimed_property_hunter.cli.notify_infrastructure_sync", fake_notify
    )

    class _Args:
        config = str(roster_path)

    assert _cmd_scheduled_extract(_Args()) == 0
    assert notified == ["roster_empty"]


def test_unexpected_csv_header_check_failed_drift_notify(tmp_path, monkeypatch):
    """Header drift persists check_failed with drift reason and notify decision."""
    zip_path = _csv_zip(
        tmp_path / "bad-header.zip",
        ["WRONG_COL", "PROPERTY_ID"],
        [["SMITH", "123"]],
    )
    with pytest.raises(HeaderDriftError):
        filter_zip_for_surnames(zip_path, ["Testsubject"])

    saved = _patch_persist(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "unclaimed_property_hunter.extract_pipeline.fingerprint_existing_zip",
        lambda _p: _fingerprint(),
    )
    notified: list[str] = []

    def fake_notify(_record, decision):
        notified.append(decision.reason)
        return {"decision_reason": decision.reason, "pages": [{"status": "sent"}]}

    monkeypatch.setattr(
        "unclaimed_property_hunter.extract_pipeline.notify_infrastructure_sync",
        fake_notify,
    )
    record = run_extract_or_persist_failure(
        surname="Testsubject",
        also=[],
        zip_path=zip_path,
        download=False,
        notify=True,
    )
    assert record.check_failed is True
    assert record.check_failure_reason.startswith("header_drift:")
    decision = decide_check_failed_notification(
        failure_reason=record.check_failure_reason,
        consecutive_check_failed=1,
    )
    assert decision.reason == "header_drift"
    assert notified == ["header_drift"]
    assert saved
