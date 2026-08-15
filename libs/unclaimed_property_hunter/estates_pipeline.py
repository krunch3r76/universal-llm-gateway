"""Persist an estates-xlsx extract as a dated hunter run.

Callers: CLI ``estates``. Does not page — estates hits are a different
surface from All_Records notify. Never invents rows.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from unclaimed_property_hunter.estates_extract import (
    download_estates_xlsx,
    filter_xlsx_for_needles,
    fingerprint_existing_xlsx,
    hits_from_rows,
)
from unclaimed_property_hunter.extract_pipeline import run_id, utc_now
from unclaimed_property_hunter.models import CorpusFingerprint, Query, RunRecord
from unclaimed_property_hunter.record import persist_run, write_raw_and_normalized
from unclaimed_property_hunter.surfaces import ESTATES_XLSX_URL


def run_estates(
    *,
    surname: str,
    xlsx_path: Path,
    download: bool,
    also: list[str] | None = None,
    substring: list[str] | None = None,
) -> RunRecord:
    """Download or reuse the estates workbook, filter needles, persist the run."""
    when = utc_now()
    fingerprint: CorpusFingerprint
    if download or not xlsx_path.is_file():
        result = download_estates_xlsx(xlsx_path)
        xlsx_path = result.path
        fingerprint = result.fingerprint
    else:
        fingerprint = fingerprint_existing_xlsx(xlsx_path)
    tokens = [surname, *(also or [])]
    rows, scanned = filter_xlsx_for_needles(xlsx_path, tokens, substring)
    hits = hits_from_rows(rows)
    fingerprint = replace(fingerprint, rows_scanned=scanned)
    intended = f"estates_xlsx tokens={tokens!r}"
    if substring:
        intended = intended + f" substring={list(substring)!r}"
    query = Query(
        surname=surname,
        intended_query_string=intended,
        exact_http_request=f"GET {ESTATES_XLSX_URL} filter tokens={tokens!r}",
        endpoint_url=ESTATES_XLSX_URL,
    )
    notes = f"estates_extract scanned={scanned} matched={len(rows)} xlsx={xlsx_path}"
    record = RunRecord(
        run_id=run_id(f"estates-{surname}", when),
        utc_timestamp=when,
        query=query,
        run_kind="estates_extract",
        search_executed=True,
        raw_payload_uri="",
        raw_sha256="",
        hits=hits,
        notes=notes,
        corpus_fingerprint=fingerprint,
    )
    raw = json.dumps(
        {
            "scanned": scanned,
            "tokens": tokens,
            "substring": list(substring or []),
            "hits": rows,
            "corpus_fingerprint": {
                "url": fingerprint.url,
                "last_modified": fingerprint.last_modified,
                "etag": fingerprint.etag,
                "content_length": fingerprint.content_length,
                "zip_sha256": fingerprint.zip_sha256,
                "rows_scanned": fingerprint.rows_scanned,
                "corpus_source": fingerprint.corpus_source,
            },
        },
        indent=2,
    ).encode()
    record = write_raw_and_normalized(record, raw)
    persist_run(record)
    return record
