from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from research_dialogue._types import Snapshot
from research_dialogue.ledger_read import read_status
from research_dialogue.session import run_close, run_turn

OUR_MODEL = "anthropic/claude-opus-5-5"
_OPEN_WAIT_POLL_S = 30
_OPEN_WAIT_MAX_S = 600
_CLOUD_PROXY_PIDFILE = Path.home() / ".gateway" / "cloud-proxy.pid"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def probe_pids(
    *,
    expect_stargate_pid: str,
    expect_cloud_proxy_pid: str,
    ss_output: str | None = None,
    pidfile_text: str | None = None,
) -> tuple[bool, dict[str, Any]]:
    if ss_output is None:
        ss_output = subprocess.run(
            ["ss", "-ltnpH", "sport = :9999"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
    stargate_pid = None
    if "pid=" in ss_output:
        stargate_pid = ss_output.split("pid=")[1].split(",")[0]
    if pidfile_text is None:
        try:
            pidfile_text = _CLOUD_PROXY_PIDFILE.read_text(encoding="utf-8").strip()
        except OSError:
            pidfile_text = None
    cloud_pid = pidfile_text
    pids = {"stargate_9999": stargate_pid, "cloud_proxy_pidfile": cloud_pid}
    ok = stargate_pid == str(expect_stargate_pid) and cloud_pid == str(expect_cloud_proxy_pid)
    return ok, pids


def ledger_path_from_snapshot(snap: Snapshot) -> Path:
    raw = snap.raw.get("ledger_path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("status payload missing ledger_path")
    return Path(raw)


def ledger_max_id(ledger_path: Path) -> int:
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    try:
        row = conn.execute("select coalesce(max(id),0) from entries").fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def ledger_entries_after(ledger_path: Path, max_id: int) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{ledger_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "select * from entries where id > ? order by id",
            (max_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def wait_open_clear(
    snap: Snapshot,
    *,
    read_fn: Callable[[], Snapshot] = read_status,
    poll_s: int = _OPEN_WAIT_POLL_S,
    max_wait_s: int = _OPEN_WAIT_MAX_S,
) -> tuple[Snapshot, list[dict[str, Any]]]:
    waits: list[dict[str, Any]] = []
    st = snap
    waited = 0
    while st.open_reservations_usd > 0 and waited < max_wait_s:
        waits.append(
            {
                "t": _now_iso(),
                "open_usd": str(st.open_reservations_usd),
            }
        )
        time.sleep(poll_s)
        waited += poll_s
        st = read_fn()
    return st, waits


def attribute_ledger_window(
    new_entries: list[dict[str, Any]],
    *,
    our_model: str = OUR_MODEL,
    harness_billed: Decimal | None,
) -> tuple[dict[str, Any], str | None]:
    """Return attribution fields and optional driver_stop reason."""
    rec: dict[str, Any] = {}
    reqs: dict[str, list[dict[str, Any]]] = {}
    for entry in new_entries:
        reqs.setdefault(str(entry["request_id"]), []).append(entry)
    ours = [
        rid
        for rid, es in reqs.items()
        if any(x.get("model") == our_model and x.get("kind") == "reserve" for x in es)
    ]
    rec["foreign_request_ids"] = [rid for rid in reqs if rid not in ours]
    rec["our_request_ids"] = ours
    stop: str | None = None
    if len(ours) == 1:
        es = reqs[ours[0]]
        res_usd = sum(Decimal(str(x["usd"])) for x in es if x.get("kind") == "reserve")
        com = [x for x in es if x.get("kind") == "commit"]
        com_usd = sum(Decimal(str(x["usd"])) for x in com)
        rec["ledger_reserve"] = str(res_usd)
        rec["ledger_commit"] = str(com_usd) if com else None
        if com and harness_billed is not None:
            gap = com_usd - harness_billed
            rec["gap"] = str(gap)
            if com_usd == res_usd and com_usd != harness_billed:
                rec["finalize_branch_inferred"] = "floor (commit == reservation)"
            elif com_usd == harness_billed:
                rec["finalize_branch_inferred"] = "exact (commit == billed)"
            else:
                rec["finalize_branch_inferred"] = "other (commit != reservation, != billed)"
            if com_usd < harness_billed:
                stop = "ledger_undercount"
        elif not com:
            rec["finalize_branch_inferred"] = "no commit yet (open)"
    elif len(ours) > 1:
        rec["attribution"] = "ambiguous: >1 opus-5-5 request in window"
        stop = "attribution_ambiguous"
    else:
        rec["attribution"] = "no opus-5-5 entry in window"
    rec["driver_stop"] = stop
    return rec, stop


def _status_subset(snap: Snapshot) -> dict[str, Any]:
    raw = snap.raw
    return {
        "committed_usd": str(snap.committed_usd),
        "open_reservations_usd": str(snap.open_reservations_usd),
        "healthy": raw.get("healthy"),
        "window_start": snap.window_start,
        "ledger_instance_id": snap.ledger_instance_id,
        "latch": raw.get("latch"),
    }


def run_driver(
    mode: str,
    session_dir: Path,
    *,
    expect_stargate_pid: str,
    expect_cloud_proxy_pid: str,
    stop_reason: str | None = None,
    challenge_file: Path | None = None,
    read_fn: Callable[[], Snapshot] | None = None,
    ss_output: str | None = None,
    pidfile_text: str | None = None,
    sleep_after: Callable[[float], None] = time.sleep,
    open_wait_max_s: int = _OPEN_WAIT_MAX_S,
) -> tuple[int, dict[str, Any]]:
    read_fn = read_fn or read_status
    rec: dict[str, Any] = {"mode": mode, "t_start": _now_iso()}
    ok, pids = probe_pids(
        expect_stargate_pid=expect_stargate_pid,
        expect_cloud_proxy_pid=expect_cloud_proxy_pid,
        ss_output=ss_output,
        pidfile_text=pidfile_text,
    )
    rec["pids"] = pids
    if not ok:
        rec["abort"] = "pid_changed_or_restart"
        return 5, rec

    st = read_fn()
    st, open_waits = wait_open_clear(st, read_fn=read_fn, max_wait_s=open_wait_max_s)
    if open_waits:
        rec["open_waits"] = open_waits
    if st.open_reservations_usd > 0:
        rec["abort"] = "foreign_open_reservation"
        rec["pre_status"] = _status_subset(st)
        return 6, rec

    rec["pre_status"] = _status_subset(st)
    ledger_path = ledger_path_from_snapshot(st)
    m0 = ledger_max_id(ledger_path)
    rec["ledger_max_id_pre"] = m0
    rec["ledger_path"] = str(ledger_path)

    cli_exit = 0
    session_line: dict[str, Any] = {}
    if mode == "turn":
        cli_exit, session_line = run_turn(
            session_dir,
            challenge_file=challenge_file,
            read_fn=read_fn,
        )
    elif mode == "close":
        if not stop_reason:
            raise ValueError("close requires stop_reason")
        cli_exit, session_line = run_close(
            session_dir,
            stop_reason=stop_reason,
            read_fn=read_fn,
        )
    else:
        raise ValueError(f"unknown mode {mode!r}")

    rec["cli_exit"] = cli_exit
    rec["session_line"] = session_line
    sleep_after(1)
    new_entries = ledger_entries_after(ledger_path, m0)
    rec["new_entries"] = new_entries

    state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    n = int(state.get("turn_count", 0))
    rec["turn"] = n
    tf = session_dir / f"turn-{n:02d}.json"
    billed: Decimal | None = None
    if tf.exists():
        td = json.loads(tf.read_text(encoding="utf-8"))
        billed = Decimal(str(td.get("billed_usd", "0")))
        rec["harness_billed"] = str(billed)
        rec["usage"] = td.get("usage")
        rec["stop_reason_api"] = td.get("stop_reason")

    attr, driver_stop = attribute_ledger_window(new_entries, harness_billed=billed)
    rec.update(attr)
    rec["t_end"] = _now_iso()
    post = read_fn()
    rec["post_status"] = _status_subset(post)

    tag = f"turn-{n:02d}" if mode == "turn" else f"close-{n:02d}"
    (session_dir / f"{tag}-ledger.json").write_text(
        json.dumps(rec, indent=2, default=str) + "\n",
        encoding="utf-8",
    )

    summary = {
        k: rec.get(k)
        for k in (
            "turn",
            "cli_exit",
            "harness_billed",
            "ledger_reserve",
            "ledger_commit",
            "gap",
            "finalize_branch_inferred",
            "foreign_request_ids",
            "driver_stop",
            "stop_reason_api",
        )
    }
    summary["cli"] = json.dumps(session_line, default=str)[-600:]
    if cli_exit != 0:
        summary["session_exit"] = cli_exit

    if driver_stop == "ledger_undercount":
        return 4, summary
    if driver_stop:
        return 7, summary
    return cli_exit if cli_exit != 0 else 0, summary


def _add_session_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--session", type=Path, required=True)
    sp.add_argument("--expect-stargate-pid", required=True)
    sp.add_argument("--expect-cloud-proxy-pid", required=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="research_dialogue.driver")
    sub = parser.add_subparsers(dest="mode", required=True)

    turn_p = sub.add_parser("turn")
    _add_session_args(turn_p)
    turn_p.add_argument("--challenge-file", type=Path, default=None)

    close_p = sub.add_parser("close")
    _add_session_args(close_p)
    close_p.add_argument("--stop-reason", required=True)

    args = parser.parse_args(argv)
    stop_reason = getattr(args, "stop_reason", None)
    code, summary = run_driver(
        args.mode,
        args.session,
        expect_stargate_pid=args.expect_stargate_pid,
        expect_cloud_proxy_pid=args.expect_cloud_proxy_pid,
        stop_reason=stop_reason,
        challenge_file=getattr(args, "challenge_file", None),
    )
    print(json.dumps(summary, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
