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
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "libs"))

from cortex_store.assertion_enrichment_backfill import (  # noqa: E402
    BackfillCountReport,
    Kind,
    attempts_for,
    count_backfill_candidates,
    events_array_len,
    fetch_candidate_batch,
    field_populated,
    load_state,
    pipeline_payload,
    prospective_question_form_share,
    record_outcome,
    save_state,
)
from cortex_store.event_publisher import record as emit_event  # noqa: E402

CORTEX_DB = Path.home() / ".cortex" / "cortex.db"
DEFAULT_STATE = Path.home() / ".cortex" / "assertion-enrichment-backfill.state.json"
DEFAULT_LOG = Path.home() / ".cortex" / "assertion-enrichment-backfill.log"
STARGATE_URL = os.environ.get("STARGATE_URL", "http://localhost:9999")
_REQUEST_TIMEOUT = 120.0
_MAX_ATTEMPTS = 3  # initial + 2 retries


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
) -> tuple[int, bool, str]:
    aid = int(row["id"])
    payload = pipeline_payload(row, kinds=kinds, redo=redo)
    try:
        resp = await client.post(
            f"{STARGATE_URL}/v1/chat/completions", json=payload
        )
        ok = resp.status_code < 400
        detail = "OK" if ok else f"HTTP {resp.status_code}"
        if not ok:
            detail += f" {resp.text[:200]}"
    except Exception as exc:
        ok = False
        detail = f"ERR {exc}"
    return aid, ok, detail


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
) -> None:
    sem = asyncio.Semaphore(concurrency)
    kind_list = list(kinds)
    permanent_failed: set[int] = set()

    for key, entry in list(state.get("outcomes", {}).items()):
        if entry.get("status") == "failed":
            permanent_failed.add(int(key))

    async def _one(row: dict[str, Any], client: httpx.AsyncClient) -> None:
        aid = int(row["id"])
        if aid in permanent_failed:
            return
        prior_attempts = attempts_for(state, aid)
        if prior_attempts >= _MAX_ATTEMPTS:
            permanent_failed.add(aid)
            return

        async with sem:
            for attempt in range(prior_attempts, _MAX_ATTEMPTS):
                _, ok, detail = await _dispatch(
                    client, row, kinds=kind_list, redo=redo
                )
                if ok:
                    conn = sqlite3.connect(str(db_path))
                    conn.row_factory = sqlite3.Row
                    verified = all(field_populated(conn, aid, k) for k in kinds)
                    conn.close()
                    if verified:
                        record_outcome(state, aid, "ok", attempts=attempt + 1)
                        state["stats"]["ok"] = state["stats"].get("ok", 0) + 1
                        state["cursor_id"] = max(state.get("cursor_id", 0), aid)
                        save_state(state_path, state)
                        _log(log_path, f"id={aid} ok attempt={attempt + 1}")
                        break
                    ok = False
                    detail = "pipeline OK but field not written"
                if not ok:
                    if attempt < _MAX_ATTEMPTS - 1:
                        backoff = (2**attempt) + random.uniform(0, 0.5)
                        _log(
                            log_path,
                            f"id={aid} retry {detail} sleep={backoff:.1f}s",
                        )
                        await asyncio.sleep(backoff)
                        continue
                    record_outcome(state, aid, "failed", attempts=attempt + 1)
                    state["stats"]["failed"] = state["stats"].get("failed", 0) + 1
                    permanent_failed.add(aid)
                    save_state(state_path, state)
                    _log(log_path, f"id={aid} failed {detail}")
                if delay_ms > 0:
                    await asyncio.sleep(delay_ms / 1000.0)

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
        await asyncio.gather(*[_one(r, client) for r in rows])


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
        cursor = max(int(r["id"]) for r in rows)
        state["cursor_id"] = cursor
        save_state(state_path, state)
        processed += len(rows)
        if limit and processed >= limit:
            break

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
    before = [r["prospective_summary"] for r in rows]
    share_before = prospective_question_form_share(before)
    print(f"## prospective sample (existing, n={len(rows)})")
    print(f"- starts with What: {share_before * 100:.1f}%")

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

    share_after = prospective_question_form_share(after_rows)
    print(f"- after redo starts with What: {share_after * 100:.1f}%")
    examples: list[tuple[int, str, str]] = []
    for row, before_t, after_t in zip(rows, before, after_rows, strict=True):
        if len(examples) < 3 and after_t:
            examples.append((row["id"], before_t or "", after_t or ""))
    for aid, b, a in examples:
        print(f"\n### id={aid}\nBEFORE: {b[:400]}\nAFTER:  {a[:400]}")
    if share_after > 0.15:
        print(
            "\nAC4 FAIL: What-share > 15% — run events-only full backfill, "
            "not prospective redo."
        )
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
