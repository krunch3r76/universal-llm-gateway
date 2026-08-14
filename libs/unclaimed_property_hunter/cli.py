"""One-shot CLI for CA SCO ClaimIt: probe, bulk-extract, ingest pasted results, diff.

Callers invoke `scripts/unclaimed-property-hunt`. Sweep never claims a completed
surname search. `extract` filters the public All_Records zip (search_executed
true). Ingest records operator-pasted JSON/HTML from the interactive UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from unclaimed_property_hunter.bulk_extract import (
    download_bulk_zip,
    filter_zip_for_surnames,
    hits_from_rows,
)
from unclaimed_property_hunter.diff_runs import RunDiff
from unclaimed_property_hunter.ingest import parse_html_hits, parse_json_hits
from unclaimed_property_hunter.models import Query, RunRecord
from unclaimed_property_hunter.record import (
    diff_latest,
    persist_run,
    write_raw_and_normalized,
)
from unclaimed_property_hunter.transport import (
    BULK_ZIP_URL,
    CLAIMIT_URL,
    intended_query_string,
    probe_transport,
)


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_id(surname: str, when: str) -> str:
    stamp = when.replace(":", "").replace("-", "")
    return f"{surname.lower()}-{stamp}"


def _print_diff(diff: RunDiff | None) -> None:
    if diff is None:
        print("diff: fewer than two persisted runs for this surname")
        return
    print(f"diff added={diff.added} disappeared={diff.disappeared} changed={len(diff.changed)}")
    for change in diff.changed:
        print(f"  changed {change.property_id}: {change.before} -> {change.after}")


def _cmd_sweep(args: argparse.Namespace) -> int:
    """Probe live ClaimIt and record a transport_probe (search_executed=false)."""
    when = _utc_now()
    intended = intended_query_string(args.surname, args.first_name, args.city)
    probe = probe_transport()
    notes = (
        f"claimit.sco.ca.gov error={probe.claimit_sco_error!r}; "
        f"ucpi status={probe.ucpi_status} location={probe.ucpi_location!r}; "
        f"landing {probe.landing_status} {probe.landing_url} "
        f"ctype={probe.landing_content_type!r} bytes={len(probe.landing_body)}"
    )
    query = Query(
        surname=args.surname,
        first_name=args.first_name,
        city=args.city,
        intended_query_string=intended,
        exact_http_request=f"GET {CLAIMIT_URL}",
        endpoint_url=probe.landing_url or CLAIMIT_URL,
    )
    record = RunRecord(
        run_id=_run_id(args.surname, when),
        utc_timestamp=when,
        query=query,
        run_kind="transport_probe",
        search_executed=False,
        raw_payload_uri="",
        raw_sha256="",
        hits=[],
        notes=notes,
    )
    raw = (
        notes
        + "\n--- landing body ---\n"
        + probe.landing_body
    ).encode()
    record = write_raw_and_normalized(record, raw)
    writes = persist_run(record)
    print(json.dumps({
        "run_id": record.run_id,
        "utc_timestamp": record.utc_timestamp,
        "intended_query_string": intended,
        "exact_http_request": query.exact_http_request,
        "endpoint_url": query.endpoint_url,
        "search_executed": False,
        "hit_count": 0,
        "hit_count_meaning": "not_a_completed_search",
        "raw_payload_uri": record.raw_payload_uri,
        "raw_sha256": record.raw_sha256,
        "notes": notes,
        "cortex": _summarize_writes(writes),
    }, indent=2))
    _print_diff(diff_latest(args.surname))
    return 0


def _summarize_writes(writes: dict) -> dict:
    run_entity = writes.get("run_entity") or {}
    run_assert = writes.get("run_assertion") or {}
    return {
        "run_entity_id": run_entity.get("id") or run_entity.get("entity_id"),
        "run_assertion_id": (run_assert.get("item") or {}).get("id")
        or run_assert.get("id")
        or run_assert.get("assertion_id"),
        "run_entity_keys": sorted(run_entity.keys()),
        "run_assertion_keys": sorted(run_assert.keys()),
        "hit_writes": len(writes.get("hits") or []),
        "run_entity": run_entity,
        "run_assertion": run_assert,
    }


def _cmd_extract(args: argparse.Namespace) -> int:
    """Filter the SCO All_Records zip and record a completed search."""
    when = _utc_now()
    zip_path = Path(args.zip_path)
    if args.download or not zip_path.is_file():
        zip_path = download_bulk_zip(zip_path)
    surnames = [args.surname, *list(args.also or [])]
    rows, scanned = filter_zip_for_surnames(zip_path, surnames)
    hits = hits_from_rows(rows)
    intended = intended_query_string(args.surname, args.first_name, args.city)
    if args.also:
        intended = intended + "&also=" + ",".join(args.also)
    query = Query(
        surname=args.surname,
        first_name=args.first_name,
        city=args.city,
        intended_query_string=intended,
        exact_http_request=f"GET {BULK_ZIP_URL} filter OWNER_NAME in {surnames!r}",
        endpoint_url=BULK_ZIP_URL,
    )
    record = RunRecord(
        run_id=_run_id(args.surname, when),
        utc_timestamp=when,
        query=query,
        run_kind="bulk_extract",
        search_executed=True,
        raw_payload_uri="",
        raw_sha256="",
        hits=hits,
        notes=f"bulk_extract scanned={scanned} matched={len(rows)} zip={zip_path}",
    )
    raw = json.dumps({"scanned": scanned, "surnames": surnames, "hits": rows}, indent=2).encode()
    record = write_raw_and_normalized(record, raw)
    writes = persist_run(record)
    prudential = [h.property_id for h in hits if h.is_prudential()]
    print(json.dumps({
        "run_id": record.run_id,
        "utc_timestamp": record.utc_timestamp,
        "intended_query_string": intended,
        "search_executed": True,
        "hit_count": len(hits),
        "rows_scanned": scanned,
        "prudential_hits": prudential,
        "raw_payload_uri": record.raw_payload_uri,
        "raw_sha256": record.raw_sha256,
        "cortex": _summarize_writes(writes),
    }, indent=2))
    if prudential:
        print(f"PRUDENTIAL-HOLDER HIT: {prudential}", file=sys.stderr)
    _print_diff(diff_latest(args.surname))
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Record operator-pasted JSON or HTML against the same provenance schema."""
    when = _utc_now()
    raw_text = Path(args.raw_file).read_text(encoding="utf-8")
    intended = intended_query_string(args.surname, args.first_name, args.city)
    kind = "ingest_unparsed"
    executed = False
    hits = []
    if args.format == "json":
        hits = parse_json_hits(raw_text)
        kind = "ingest_json"
        executed = True
    else:
        parsed = parse_html_hits(raw_text)
        if parsed is None:
            kind = "ingest_unparsed"
            executed = False
            hits = []
        else:
            kind = "ingest_html"
            executed = True
            hits = parsed
    query = Query(
        surname=args.surname,
        first_name=args.first_name,
        city=args.city,
        intended_query_string=intended,
        exact_http_request=f"INGEST file={args.raw_file} format={args.format}",
        endpoint_url=args.endpoint_url,
    )
    record = RunRecord(
        run_id=_run_id(args.surname, when),
        utc_timestamp=when,
        query=query,
        run_kind=kind,
        search_executed=executed,
        raw_payload_uri="",
        raw_sha256="",
        hits=hits,
        notes=f"ingest format={args.format} parsed_hits={len(hits)} kind={kind}",
    )
    record = write_raw_and_normalized(record, raw_text.encode())
    writes = persist_run(record)
    prudential = [h.property_id for h in hits if h.is_prudential()]
    print(json.dumps({
        "run_id": record.run_id,
        "utc_timestamp": record.utc_timestamp,
        "intended_query_string": intended,
        "search_executed": executed,
        "hit_count": len(hits),
        "prudential_hits": prudential,
        "raw_payload_uri": record.raw_payload_uri,
        "raw_sha256": record.raw_sha256,
        "cortex": _summarize_writes(writes),
    }, indent=2))
    if prudential:
        print(f"PRUDENTIAL-HOLDER HIT: {prudential}", file=sys.stderr)
    _print_diff(diff_latest(args.surname))
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    _print_diff(diff_latest(args.surname))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse argv and run sweep, ingest, or diff. Returns process exit status."""
    parser = argparse.ArgumentParser(
        description="CA SCO ClaimIt hunter — probe or ingest; never invent hits",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sweep = sub.add_parser("sweep", help="live transport probe (not a completed search)")
    sweep.add_argument("--surname", required=True)
    sweep.add_argument("--first-name", default="")
    sweep.add_argument("--city", default="")
    sweep.set_defaults(func=_cmd_sweep)
    extract = sub.add_parser("extract", help="filter SCO All_Records zip (completed search)")
    extract.add_argument("--surname", required=True)
    extract.add_argument("--also", action="append", default=[], help="extra OWNER_NAME needles")
    extract.add_argument("--first-name", default="")
    extract.add_argument("--city", default="")
    extract.add_argument("--zip-path", required=True, help="local All_Records.zip path")
    extract.add_argument("--download", action="store_true", help="GET the zip to --zip-path first")
    extract.set_defaults(func=_cmd_extract)
    ingest = sub.add_parser("ingest", help="record operator-pasted JSON/HTML")
    ingest.add_argument("--surname", required=True)
    ingest.add_argument("--first-name", default="")
    ingest.add_argument("--city", default="")
    ingest.add_argument("--raw-file", required=True)
    ingest.add_argument("--format", choices=("json", "html"), required=True)
    ingest.add_argument("--endpoint-url", default=CLAIMIT_URL)
    ingest.set_defaults(func=_cmd_ingest)
    diff = sub.add_parser("diff", help="diff the two latest persisted runs")
    diff.add_argument("--surname", required=True)
    diff.set_defaults(func=_cmd_diff)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
