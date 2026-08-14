"""One-shot CLI for CA SCO ClaimIt: probe, bulk-extract, ingest pasted results, diff.

Callers invoke `scripts/unclaimed-property-hunt`. Sweep never claims a completed
surname search. `extract` filters the public All_Records zip (search_executed
true). Ingest records operator-pasted JSON/HTML from the interactive UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from unclaimed_property_hunter.diff_runs import RunDiff
from unclaimed_property_hunter.extract_pipeline import run_extract, utc_now, run_id
from unclaimed_property_hunter.hit_notify import (
    PAGE_AMOUNT_FLOOR,
    decide_notifications,
    format_digest_note,
    notify_hit_pages_sync,
    probe_pager_from_service_context_sync,
)
from unclaimed_property_hunter.ingest import parse_html_hits, parse_json_hits
from unclaimed_property_hunter.models import Hit, Query, RunRecord
from unclaimed_property_hunter.record import diff_latest, persist_run, write_raw_and_normalized
from unclaimed_property_hunter.result_surface import format_operator_stderr, public_run_dict
from unclaimed_property_hunter.roster import (
    EXAMPLE_ROSTER_REL,
    ROSTER_PATH_ENV,
    default_roster_path,
    load_roster,
)
from unclaimed_property_hunter.cli_surfaces import register_surface_commands
from unclaimed_property_hunter.transport import CLAIMIT_URL, intended_query_string, probe_transport


def _print_diff(diff: RunDiff | None) -> None:
    if diff is None:
        print("diff: fewer than two persisted runs for this surname")
        return
    print(f"diff added={diff.added} disappeared={diff.disappeared} changed={len(diff.changed)}")
    for change in diff.changed:
        print(f"  changed {change.property_id}: {change.before} -> {change.after}")


def _summarize_writes(writes: dict) -> dict:
    run_entity = writes.get("run_entity") or {}
    run_assert = writes.get("run_assertion") or {}
    return {
        "run_entity_id": run_entity.get("id") or run_entity.get("entity_id"),
        "run_assertion_id": (run_assert.get("item") or {}).get("id")
        or run_assert.get("id")
        or run_assert.get("assertion_id"),
        "hit_writes": len(writes.get("hits") or []),
    }


def _emit_run_json(record: RunRecord, *, extra: dict | None = None) -> None:
    payload = public_run_dict(record)
    if extra:
        payload.update(extra)
    print(json.dumps(payload, indent=2))
    stderr_line = format_operator_stderr(record)
    if stderr_line:
        print(stderr_line, file=sys.stderr)


def _cmd_sweep(args: argparse.Namespace) -> int:
    when = utc_now()
    intended = intended_query_string(args.surname, args.first_name, args.city)
    probe = probe_transport()
    notes = (
        f"claimit.sco.ca.gov error={probe.claimit_sco_error!r}; "
        f"ucpi status={probe.ucpi_status} location={probe.ucpi_location!r}; "
        f"landing {probe.landing_status} {probe.landing_url}"
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
        run_id=run_id(args.surname, when),
        utc_timestamp=when,
        query=query,
        run_kind="transport_probe",
        search_executed=False,
        raw_payload_uri="",
        raw_sha256="",
        hits=[],
        notes=notes,
    )
    record = write_raw_and_normalized(record, (notes + probe.landing_body).encode())
    persist_run(record)
    _emit_run_json(record)
    _print_diff(diff_latest(args.surname))
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    record = run_extract(
        surname=args.surname,
        also=list(args.also or []),
        zip_path=Path(args.zip_path),
        download=args.download,
        first_name=args.first_name,
        city=args.city,
        notify=not args.no_notify,
    )
    _emit_run_json(record, extra={"rows_scanned": record.corpus_fingerprint.rows_scanned if record.corpus_fingerprint else None})
    _print_diff(diff_latest(args.surname))
    return 0


def _cmd_scheduled_extract(args: argparse.Namespace) -> int:
    config_path = Path(args.config).expanduser() if args.config else default_roster_path()
    if not config_path.exists():
        print(
            f"roster not found: {config_path} — copy {EXAMPLE_ROSTER_REL} there and fill "
            f"in subjects, or set {ROSTER_PATH_ENV}. Never commit the live roster.",
            file=sys.stderr,
        )
        return 2
    roster = load_roster(config_path)
    exit_code = 0
    for subject in roster.subjects:
        try:
            run_extract(
                surname=subject.surname,
                also=list(subject.also),
                zip_path=roster.zip_cache,
                download=True,
                notify=True,
            )
        except Exception as exc:
            print(f"check_failed surname={subject.surname} error={exc}", file=sys.stderr)
            exit_code = 1
    return exit_code


def _cmd_probe_pager(args: argparse.Namespace) -> int:
    skip_wait = bool(getattr(args, "no_wait", False))
    if not skip_wait:
        from pager_notify.peer_wait import wait_for_pager_peer_sync

        ready, reason = wait_for_pager_peer_sync()
        if not ready:
            print(json.dumps({"status": "failed", "reason": "peer_not_ready", "error": reason}))
            return 1
    payload = probe_pager_from_service_context_sync(skip_peer_wait=skip_wait)
    print(json.dumps(payload, indent=2))
    return 0 if payload.get("status") == "sent" else 1


def _cmd_wait_pager_peer(_args: argparse.Namespace) -> int:
    from pager_notify.peer_wait import wait_for_pager_peer_sync

    ready, reason = wait_for_pager_peer_sync()
    print(json.dumps({"ready": ready, "reason": reason}))
    return 0 if ready else 1


def _cmd_notify_test(args: argparse.Namespace) -> int:
    """Force notify paths for AC evidence — does not persist a full extract."""
    when = utc_now()
    query = Query(surname=args.surname, intended_query_string=f"lastName={args.surname}")
    base = RunRecord(
        run_id=run_id(args.surname, when),
        utc_timestamp=when,
        query=query,
        run_kind="bulk_extract",
        search_executed=True,
        raw_payload_uri="cortex://notes/system/unclaimed-property/runs/notify-test.raw",
        raw_sha256="notify-test",
        hits=[],
    )
    if args.mode == "above":
        hit = Hit(property_id="test-above", holder="Test", owner_name="Test", amount_or_range="25.00")
        record = replace(base, hits=[hit])
        decision = decide_notifications(record, RunDiff(added=["test-above"], disappeared=[], changed=[]))
        outcome = notify_hit_pages_sync(record, decision)
    elif args.mode == "below":
        hit = Hit(property_id="test-below", holder="Test", owner_name="Test", amount_or_range="0.17")
        record = replace(base, hits=[hit])
        decision = decide_notifications(record, RunDiff(added=["test-below"], disappeared=[], changed=[]))
        outcome = {"decision_reason": decision.reason, "pages": [], "digest": format_digest_note(decision.digest_hits)}
    else:
        decision = decide_notifications(base, None)
        outcome = {"decision_reason": decision.reason, "pages": []}
    print(json.dumps({"mode": args.mode, "floor": PAGE_AMOUNT_FLOOR, "outcome": outcome}, indent=2))
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    when = utc_now()
    raw_text = Path(args.raw_file).read_text(encoding="utf-8")
    intended = intended_query_string(args.surname, args.first_name, args.city)
    kind = "ingest_unparsed"
    executed = False
    hits: list[Hit] = []
    if args.format == "json":
        hits = parse_json_hits(raw_text)
        kind = "ingest_json"
        executed = True
    else:
        parsed = parse_html_hits(raw_text)
        if parsed is not None:
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
        run_id=run_id(args.surname, when),
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
    persist_run(record)
    _emit_run_json(record)
    _print_diff(diff_latest(args.surname))
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    _print_diff(diff_latest(args.surname))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse hunter subcommands and dispatch; return the command exit code."""
    parser = argparse.ArgumentParser(description="CA SCO ClaimIt hunter")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sweep = sub.add_parser("sweep")
    sweep.add_argument("--surname", required=True)
    sweep.add_argument("--first-name", default="")
    sweep.add_argument("--city", default="")
    sweep.set_defaults(func=_cmd_sweep)
    extract = sub.add_parser("extract")
    extract.add_argument("--surname", required=True)
    extract.add_argument("--also", action="append", default=[])
    extract.add_argument("--first-name", default="")
    extract.add_argument("--city", default="")
    extract.add_argument("--zip-path", required=True)
    extract.add_argument("--download", action="store_true")
    extract.add_argument("--no-notify", action="store_true")
    extract.set_defaults(func=_cmd_extract)
    sched = sub.add_parser("scheduled-extract")
    sched.add_argument("--config", default="", help=f"default: {default_roster_path()}")
    sched.set_defaults(func=_cmd_scheduled_extract)
    probe = sub.add_parser("probe-pager")
    probe.add_argument(
        "--no-wait",
        action="store_true",
        help="skip peer-wait preflight (adverse-condition probe only)",
    )
    probe.set_defaults(func=_cmd_probe_pager)
    wait_peer = sub.add_parser("wait-pager-peer")
    wait_peer.set_defaults(func=_cmd_wait_pager_peer)
    notify_test = sub.add_parser("notify-test")
    notify_test.add_argument("--surname", default="Testsubject")
    notify_test.add_argument("--mode", choices=("above", "below", "empty"), required=True)
    notify_test.set_defaults(func=_cmd_notify_test)
    ingest = sub.add_parser("ingest")
    ingest.add_argument("--surname", required=True)
    ingest.add_argument("--first-name", default="")
    ingest.add_argument("--city", default="")
    ingest.add_argument("--raw-file", required=True)
    ingest.add_argument("--format", choices=("json", "html"), required=True)
    ingest.add_argument("--endpoint-url", default=CLAIMIT_URL)
    ingest.set_defaults(func=_cmd_ingest)
    diff = sub.add_parser("diff")
    diff.add_argument("--surname", required=True)
    diff.set_defaults(func=_cmd_diff)
    register_surface_commands(sub)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
