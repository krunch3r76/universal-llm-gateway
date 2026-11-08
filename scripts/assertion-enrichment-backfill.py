#!/usr/bin/env python3
"""Resumable, unattended assertion-enrichment backfill (events + prospective).

Dispatches ``assertion-enrichment`` on Stargate with ``redo`` when overwriting.
Persists cursor + per-row outcomes; retries failed rows up to 2x with backoff.

Usage:
  ~/.venvs/universal/bin/python scripts/assertion-enrichment-backfill.py --dry-run
  ~/.venvs/universal/bin/python scripts/assertion-enrichment-backfill.py \\
      --kinds events --redo --concurrency 2 --state ~/.cortex/enrichment-backfill.json
  ~/.venvs/universal/bin/python scripts/assertion-enrichment-backfill.py \\
      --validate-prospective-sample 50
  ~/.venvs/universal/bin/python scripts/assertion-enrichment-backfill.py \\
      --sample-events 100 --redo --kinds events
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import signal
import sqlite3
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "libs"))

from cortex_store.assertion_enrichment_backfill import (  # noqa: E402
    MAX_PERMANENT_ATTEMPTS,
    PAUSE_BACKOFF_CAP_S,
    BackfillCountReport,
    DispatchErrorClass,
    Kind,
    attempts_for,
    count_backfill_candidates,
    events_array_len,
    fetch_candidate_batch,
    fetch_rows_by_ids,
    field_populated,
    load_state,
    parse_dispatch_exception,
    parse_dispatch_http,
    pipeline_payload,
    prospective_quality_metrics,
    prospective_question_form_share,
    record_outcome,
    requeue_failed_outcomes,
    save_state,
    terminal_outcome_ids,
)
from cortex_store.db import _CORTEX_DB  # noqa: E402
from cortex_store.event_publisher import record as emit_event  # noqa: E402

CORTEX_DB = _CORTEX_DB
DEFAULT_STATE = Path.home() / ".cortex" / "assertion-enrichment-backfill.state.json"
DEFAULT_LOG = Path.home() / ".cortex" / "assertion-enrichment-backfill.log"
STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_REQUEST_TIMEOUT = 120.0


class _OutageCoordinator:
    """Global pause when Stargate returns transient errors."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._backoff_s = 1.0

    async def handle_transient(
        self,
        client: httpx.AsyncClient,
        detail: str,
        log: Callable[[str], None],
    ) -> None:
        async with self._lock:
            self._backoff_s = min(self._backoff_s * 2.0, PAUSE_BACKOFF_CAP_S)
            sleep_s = self._backoff_s + random.uniform(0, 1.0)
            log(
                f"TRANSIENT global pause (cap={PAUSE_BACKOFF_CAP_S:.0f}s) "
                f"{detail[:160]} sleep={sleep_s:.1f}s"
            )
        await asyncio.sleep(sleep_s)
        while True:
            try:
                resp = await client.get(f"{STARGATE_URL}/health", timeout=15.0)
                if resp.status_code < 400:
                    log("stargate health OK — resuming")
                    async with self._lock:
                        self._backoff_s = 1.0
                    return
            except Exception as exc:
                log(f"stargate health probe ERR {exc}")
            await asyncio.sleep(5.0)


_OUTAGE = _OutageCoordinator()


def _parse_kinds(raw: str) -> tuple[Kind, ...]:
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    out: list[Kind] = []
    for p in parts:
        if p not in ("events", "prospective"):
            raise SystemExit(f"invalid kind {p!r}; use events and/or prospective")
        out.append(p)  # type: ignore[arg-type]
    return tuple(out) if out else ("events", "prospective")


def _log(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stamped = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {line}\n"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(stamped)
    print(line)


def _print_counts(report: BackfillCountReport, *, kinds: tuple[Kind, ...], redo: bool) -> None:
    print("## assertion-enrichment backfill (dry-run counts)")
    print(f"- active (superseded_by IS NULL): {report.active_total}")
    print(f"- eligible kinds={list(kinds)} redo={redo}: {report.eligible}")
    if report.skip_by_class:
        print("- skip classes:")
        for name, n in sorted(report.skip_by_class.items()):
            print(f"  - {name}: {n}")


async def _dispatch(
    client: httpx.AsyncClient,
    row: dict[str, Any],
    *,
    kinds: list[str],
    redo: bool,
):
    aid = int(row["id"])
    payload = pipeline_payload(row, kinds=kinds, redo=redo)
    try:
        resp = await client.post(
            f"{STARGATE_URL}/v1/chat/completions", json=payload
        )
        return aid, parse_dispatch_http(resp.status_code, resp.text)
    except Exception as exc:
        return aid, parse_dispatch_exception(exc)


def _should_skip_failed(state: dict[str, Any], aid: int, *, allow_failed: bool) -> bool:
    if allow_failed:
        return False
    entry = state.get("outcomes", {}).get(str(aid), {})
    return entry.get("status") == "failed"


async def _run_rows(
    rows: list[dict[str, Any]],
    *,
    kinds: tuple[Kind, ...],
    redo: bool,
    concurrency: int,
    delay_ms: int,
    db_path: Path,
    state_path: Path,
    log_path: Path,
    state: dict[str, Any],
    allow_failed: bool = False,
) -> None:
    sem = asyncio.Semaphore(concurrency)
    kind_list = list(kinds)

    def log(line: str) -> None:
        _log(log_path, line)

    async def _one(row: dict[str, Any], client: httpx.AsyncClient) -> None:
        aid = int(row["id"])
        if _should_skip_failed(state, aid, allow_failed=allow_failed):
            return
        permanent_attempts = attempts_for(state, aid)

        async with sem:
            while permanent_attempts < MAX_PERMANENT_ATTEMPTS:
                _, result = await _dispatch(
                    client, row, kinds=kind_list, redo=redo
                )
                if result.ok:
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    verified = all(field_populated(conn, aid, k) for k in kinds)
                    conn.close()
                    if verified:
                        record_outcome(state, aid, "ok", attempts=permanent_attempts + 1)
                        state["stats"]["ok"] = state["stats"].get("ok", 0) + 1
                        state["cursor_id"] = max(state.get("cursor_id", 0), aid)
                        save_state(state_path, state)
                        log(f"id={aid} ok permanent_attempts={permanent_attempts + 1}")
                        break
                    result = parse_dispatch_http(
                        500, "pipeline OK but field not written"
                    )
                if not result.ok and (
                    result.error_class == DispatchErrorClass.TRANSIENT
                ):
                    await _OUTAGE.handle_transient(client, result.detail, log)
                    continue
                permanent_attempts += 1
                if permanent_attempts < MAX_PERMANENT_ATTEMPTS:
                    backoff = (2 ** (permanent_attempts - 1)) + random.uniform(0, 0.5)
                    log(
                        f"id={aid} PERMANENT retry ({permanent_attempts}/"
                        f"{MAX_PERMANENT_ATTEMPTS}) {result.detail[:120]} "
                        f"sleep={backoff:.1f}s"
                    )
                    await asyncio.sleep(backoff)
                    continue
                record_outcome(
                    state,
                    aid,
                    "failed",
                    attempts=permanent_attempts,
                    last_error=result.detail,
                    error_class=DispatchErrorClass.PERMANENT.value,
                )
                state["stats"]["failed"] = state["stats"].get("failed", 0) + 1
                save_state(state_path, state)
                log(f"id={aid} failed permanent {result.detail[:200]}")
                break
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        await asyncio.gather(*[_one(r, client) for r in rows])


def _install_sigterm_save(
    state_path: Path, log_path: Path, state_holder: list[dict[str, Any]]
) -> None:
    def _handler(_signum: int, _frame: object) -> None:
        save_state(state_path, state_holder[0])
        _log(log_path, "SIGTERM saved state")
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, _handler)


async def _main_run(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    if not db_path.is_file():
        print(f"ERROR: missing db {db_path}", file=sys.stderr)
        return 1

    kinds = _parse_kinds(args.kinds)
    redo = bool(args.redo)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    if args.dry_run:
        report = count_backfill_candidates(conn, kinds=kinds, redo=redo)
        conn.close()
        _print_counts(report, kinds=kinds, redo=redo)
        return 0

    state_path = Path(args.state).expanduser()
    log_path = Path(args.log).expanduser()
    state = load_state(state_path)
    state_holder = [state]
    _install_sigterm_save(state_path, log_path, state_holder)

    requeued_ids: list[int] = requeue_failed_outcomes(state, transient_only=True)
    if args.retry_failed:
        requeued_ids.extend(
            requeue_failed_outcomes(state, transient_only=False)
        )
    requeued_ids = sorted(set(requeued_ids))
    if requeued_ids:
        save_state(state_path, state)
        _log(log_path, f"requeued_failed count={len(requeued_ids)} ids={requeued_ids[:20]}")
        warmup = fetch_rows_by_ids(conn, requeued_ids)
        if warmup:
            await _run_rows(
                warmup,
                kinds=kinds,
                redo=redo,
                concurrency=args.concurrency,
                delay_ms=args.delay_ms,
                db_path=db_path,
                state_path=state_path,
                log_path=log_path,
                state=state,
                allow_failed=True,
            )

    cursor = int(state.get("cursor_id", 0))
    processed = 0
    limit = args.limit

    while True:
        batch_size = min(args.batch_size, limit - processed) if limit else args.batch_size
        if batch_size <= 0:
            break
        rows = fetch_candidate_batch(
            conn,
            kinds=kinds,
            redo=redo,
            after_id=cursor,
            limit=batch_size,
        )
        if not rows:
            break
        _log(log_path, f"batch size={len(rows)} after_id={cursor}")
        await _run_rows(
            rows,
            kinds=kinds,
            redo=redo,
            concurrency=args.concurrency,
            delay_ms=args.delay_ms,
            db_path=db_path,
            state_path=state_path,
            log_path=log_path,
            state=state,
        )
        terminals = terminal_outcome_ids(state)
        batch_high = max(
            (int(r["id"]) for r in rows if int(r["id"]) in terminals),
            default=cursor,
        )
        cursor = max(cursor, batch_high)
        state["cursor_id"] = cursor
        save_state(state_path, state)
        processed += len(rows)
        if limit and processed >= limit:
            break

    failed_ids = [
        int(k)
        for k, v in state.get("outcomes", {}).items()
        if v.get("status") == "failed"
    ]
    if failed_ids and not state.get("retry_sweep_done"):
        sweep_ids = requeue_failed_outcomes(state, transient_only=False)
        _log(log_path, f"end-of-pass retry sweep requeued={len(sweep_ids)}")
        retry_rows = fetch_rows_by_ids(conn, sweep_ids or failed_ids)
        if retry_rows:
            await _run_rows(
                retry_rows,
                kinds=kinds,
                redo=redo,
                concurrency=args.concurrency,
                delay_ms=args.delay_ms,
                db_path=db_path,
                state_path=state_path,
                log_path=log_path,
                state=state,
                allow_failed=True,
            )
        state["retry_sweep_done"] = True
        save_state(state_path, state)

    conn.close()
    summary = {
        "kinds": list(kinds),
        "redo": redo,
        "cursor_id": state.get("cursor_id", 0),
        **state.get("stats", {}),
    }
    emit_event("cortex.assertion_enrichment.backfill.summary", **summary)
    _log(log_path, f"run complete {summary}")
    return 0


async def _validate_prospective_sample(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    n = args.validate_prospective_sample
    rows = conn.execute(
        "SELECT id, claim, entity_id, confidence, prospective_summary "
        "FROM assertions WHERE superseded_by IS NULL "
        "AND prospective_summary IS NOT NULL AND TRIM(prospective_summary) != '' "
        "ORDER BY RANDOM() LIMIT ?",
        (n,),
    ).fetchall()
    conn.close()
    before_rows = [
        (r["prospective_summary"], r["claim"], r["entity_id"]) for r in rows
    ]
    gen_before, len_before = prospective_quality_metrics(before_rows)
    what_before = prospective_question_form_share([b[0] for b in before_rows])
    print(f"## prospective sample (existing, n={len(rows)})")
    print(f"- generic_cue_share: {gen_before * 100:.1f}%")
    print(f"- length_compliance (<=2 cues): {len_before * 100:.1f}%")
    print(f"- starts with What (diagnostic): {what_before * 100:.1f}%")

    kind_list = ["prospective"]
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        for row in rows:
            await _dispatch(
                client,
                dict(row),
                kinds=kind_list,
                redo=True,
            )
            if args.delay_ms > 0:
                await asyncio.sleep(args.delay_ms / 1000.0)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    after_rows = []
    for row in rows:
        fresh = conn.execute(
            "SELECT prospective_summary FROM assertions WHERE id = ?",
            (row["id"],),
        ).fetchone()
        after_rows.append(fresh["prospective_summary"] if fresh else None)
    conn.close()

    after_quality_rows = [
        (after_rows[i], rows[i]["claim"], rows[i]["entity_id"])
        for i in range(len(rows))
    ]
    gen_after, len_after = prospective_quality_metrics(after_quality_rows)
    what_after = prospective_question_form_share(after_rows)
    print(f"- after redo generic_cue_share: {gen_after * 100:.1f}%")
    print(f"- after redo length_compliance: {len_after * 100:.1f}%")
    print(f"- after redo starts with What (diagnostic): {what_after * 100:.1f}%")
    examples: list[tuple[int, str, str, str]] = []
    for row, before_t, after_t in zip(rows, [b[0] for b in before_rows], after_rows, strict=True):
        if len(examples) < 3 and after_t:
            examples.append((row["id"], row["claim"][:120], before_t or "", after_t or ""))
    for aid, claim, b, a in examples:
        print(f"\n### id={aid} claim={claim!r}\nBEFORE: {b[:400]}\nAFTER:  {a[:400]}")
    ac4_pass = gen_after <= 0.15 and len_after >= 0.90
    print(f"\nAC4 gate (generic<=15% and length>=90%): {'PASS' if ac4_pass else 'FAIL'}")
    if not ac4_pass:
        print("Full run: events-only (omit prospective).")
        return 2
    return 0


async def _sample_events(args: argparse.Namespace) -> int:
    db_path = Path(args.db).expanduser()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = fetch_candidate_batch(
        conn,
        kinds=("events",),
        redo=True,
        after_id=0,
        limit=args.sample_events,
    )
    conn.close()
    if not rows:
        print("No sample rows")
        return 1

    state = load_state(Path(args.state).expanduser())
    log_path = Path(args.log).expanduser()
    await _run_rows(
        rows,
        kinds=("events",),
        redo=True,
        concurrency=args.concurrency,
        delay_ms=args.delay_ms,
        db_path=db_path,
        state_path=Path(args.state).expanduser(),
        log_path=log_path,
        state=state,
    )

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    lengths: list[int] = []
    trunc = 0
    for row in rows:
        fresh = conn.execute(
            "SELECT events_json FROM assertions WHERE id = ?", (row["id"],)
        ).fetchone()
        ln = events_array_len(fresh["events_json"] if fresh else None)
        if ln < 0:
            trunc += 1
        elif ln >= 0:
            lengths.append(ln)
    conn.close()
    avg = sum(lengths) / len(lengths) if lengths else 0.0
    print("## events sample")
    print(f"- n={len(rows)} truncation_failures={trunc} avg_events={avg:.2f}")
    return 0 if trunc == 0 else 1


def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default=str(CORTEX_DB))
    p.add_argument("--state", default=str(DEFAULT_STATE))
    p.add_argument("--log", default=str(DEFAULT_LOG))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--kinds", default="events,prospective")
    p.add_argument("--redo", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--concurrency", type=int, default=2)
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--delay-ms", type=int, default=250)
    p.add_argument("--validate-prospective-sample", type=int, default=0)
    p.add_argument("--sample-events", type=int, default=0)
    p.add_argument(
        "--retry-failed",
        action="store_true",
        help="Requeue all failed rows before the main pass",
    )
    return p.parse_args()


def main() -> int:
    args = _parse()
    if args.validate_prospective_sample:
        return asyncio.run(_validate_prospective_sample(args))
    if args.sample_events:
        return asyncio.run(_sample_events(args))
    return asyncio.run(_main_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
