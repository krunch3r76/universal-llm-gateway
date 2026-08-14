"""Shared bulk-extract + notify pipeline for CLI and systemd."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from unclaimed_property_hunter.bulk_extract import (
    download_bulk_zip,
    filter_zip_for_surnames,
    fingerprint_existing_zip,
    hits_from_rows,
)
from unclaimed_property_hunter.hit_notify import (
    decide_notifications,
    format_digest_note,
    notify_hit_pages_sync,
)
from unclaimed_property_hunter.models import CorpusFingerprint, Hit, Query, RunRecord
from unclaimed_property_hunter.record import (
    consecutive_check_failed_runs,
    diff_latest,
    persist_run,
    prior_runs_for_surname,
    write_raw_and_normalized,
)
from unclaimed_property_hunter.transport import BULK_ZIP_URL, intended_query_string


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_id(surname: str, when: str) -> str:
    stamp = when.replace(":", "").replace("-", "")
    return f"{surname.lower()}-{stamp}"


def run_extract(
    *,
    surname: str,
    also: list[str],
    zip_path: Path,
    download: bool,
    first_name: str = "",
    city: str = "",
    notify: bool = True,
    force_hits: list[Hit] | None = None,
) -> RunRecord:
    """Download/filter zip, persist run, optionally notify on new hits."""
    when = utc_now()
    fingerprint: CorpusFingerprint
    if download or not zip_path.is_file():
        result = download_bulk_zip(zip_path)
        zip_path = result.path
        fingerprint = result.fingerprint
    else:
        fingerprint = fingerprint_existing_zip(zip_path)
    surnames = [surname, *also]
    rows, scanned = filter_zip_for_surnames(zip_path, surnames)
    hits = force_hits if force_hits is not None else hits_from_rows(rows)
    fingerprint = replace(fingerprint, rows_scanned=scanned)
    intended = intended_query_string(surname, first_name, city)
    if also:
        intended = intended + "&also=" + ",".join(also)
    query = Query(
        surname=surname,
        first_name=first_name,
        city=city,
        intended_query_string=intended,
        exact_http_request=f"GET {BULK_ZIP_URL} filter OWNER_NAME in {surnames!r}",
        endpoint_url=BULK_ZIP_URL,
    )
    prior_paths = prior_runs_for_surname(surname)
    prior_diff = None
    if prior_paths:
        from unclaimed_property_hunter.record import load_run_from_normalized
        from unclaimed_property_hunter.diff_runs import diff_runs

        previous = load_run_from_normalized(prior_paths[-1])
        prior_diff = diff_runs(
            previous,
            RunRecord(
                run_id="pending",
                utc_timestamp=when,
                query=query,
                run_kind="bulk_extract",
                search_executed=True,
                raw_payload_uri="",
                raw_sha256="",
                hits=hits,
            ),
        )
    notify_outcome = None
    notes = f"bulk_extract scanned={scanned} matched={len(rows)} zip={zip_path}"
    if notify:
        streak = consecutive_check_failed_runs(surname)
        pending = RunRecord(
            run_id=run_id(surname, when),
            utc_timestamp=when,
            query=query,
            run_kind="bulk_extract",
            search_executed=True,
            raw_payload_uri="",
            raw_sha256="",
            hits=hits,
            notes=notes,
            corpus_fingerprint=fingerprint,
        )
        decision = decide_notifications(pending, prior_diff, consecutive_check_failed=streak)
        digest_note = format_digest_note(decision.digest_hits)
        if digest_note:
            notes = f"{notes}; {digest_note}"
        if decision.page_hits:
            notify_outcome = notify_hit_pages_sync(pending, decision)
            if any(p.get("status") != "sent" for p in notify_outcome.get("pages", [])):
                notes = f"{notes}; notify_failed"
        else:
            notify_outcome = {"decision_reason": decision.reason, "pages": []}
    record = RunRecord(
        run_id=run_id(surname, when),
        utc_timestamp=when,
        query=query,
        run_kind="bulk_extract",
        search_executed=True,
        raw_payload_uri="",
        raw_sha256="",
        hits=hits,
        notes=notes,
        corpus_fingerprint=fingerprint,
        notify_outcome=notify_outcome,
    )
    raw = json.dumps(
        {
            "scanned": scanned,
            "surnames": surnames,
            "hits": rows,
            "corpus_fingerprint": {
                "url": fingerprint.url,
                "last_modified": fingerprint.last_modified,
                "etag": fingerprint.etag,
                "content_length": fingerprint.content_length,
                "zip_sha256": fingerprint.zip_sha256,
                "rows_scanned": fingerprint.rows_scanned,
            },
        },
        indent=2,
    ).encode()
    record = write_raw_and_normalized(record, raw)
    persist_run(record)
    return record
