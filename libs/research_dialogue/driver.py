from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from transport_utils.gateway_dir import passwd_home

from research_dialogue._types import Snapshot
from research_dialogue.ledger_read import read_status
from research_dialogue.ledger_sqlite import ledger_entries_after, ledger_max_id
from research_dialogue.models import normalize_ledger_model
from research_dialogue.session import run_close, run_turn

OUR_MODEL = normalize_ledger_model("claude-opus-5-5")
_OPEN_WAIT_POLL_S = 30
_OPEN_WAIT_MAX_S = 600


def resolve_gateway_dir(override: str | Path | None = None) -> Path:
    if override is not None:
        if isinstance(override, Path):
            if str(override):
                return override
        elif str(override).strip():
            return Path(str(override).strip())
    env = os.environ.get("RESEARCH_DIALOGUE_GATEWAY_DIR")
    if env and env.strip():
        return Path(env.strip())
    return passwd_home(strict=True) / ".gateway"


def cloud_proxy_pidfile_path(gateway_dir: str | Path | None = None) -> Path:
    return resolve_gateway_dir(gateway_dir) / "cloud-proxy.pid"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def probe_pids(
    *,
    expect_stargate_pid: str,
    expect_cloud_proxy_pid: str,
    ss_output: str | None = None,
    pidfile_text: str | None = None,
    gateway_dir: str | Path | None = None,
) -> tuple[bool, dict[str, Any]]:
    import subprocess

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
    pids: dict[str, Any] = {"stargate_9999": stargate_pid}
    if pidfile_text is None:
        pidfile_path = cloud_proxy_pidfile_path(gateway_dir)
        pids["cloud_proxy_pidfile_path"] = str(pidfile_path)
        try:
            pidfile_text = pidfile_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            pids["cloud_proxy_pidfile_error"] = f"{type(exc).__name__}: {pidfile_path}"
            pidfile_text = None
    else:
        pids["cloud_proxy_pidfile_path"] = "<injected>"
    cloud_pid = pidfile_text
    pids["cloud_proxy_pidfile"] = cloud_pid
    ok = stargate_pid == str(expect_stargate_pid) and cloud_pid == str(expect_cloud_proxy_pid)
    return ok, pids


def ledger_path_from_snapshot(snap: Snapshot) -> Path:
    raw = snap.raw.get("ledger_path")
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("status payload missing ledger_path")
    return Path(raw)


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


def _pid_mismatch_abort_detail(
    pids: dict[str, Any],
    *,
    expect_stargate_pid: str,
    expect_cloud_proxy_pid: str,
) -> str:
    path = pids.get("cloud_proxy_pidfile_path", "<unknown>")
    cloud = pids.get("cloud_proxy_pidfile")
    cloud_display = cloud if cloud is not None else "<missing>"
    stargate_obs = pids.get("stargate_9999")
    return (
        f"pidfile={path}; read cloud-proxy pid={cloud_display}; "
        f"expected stargate pid={expect_stargate_pid} cloud-proxy pid={expect_cloud_proxy_pid}; "
        f"observed stargate_9999={stargate_obs}"
    )


def preflight_guard(
    *,
    expect_stargate_pid: str,
    expect_cloud_proxy_pid: str,
    read_fn: Callable[[], Snapshot],
    ss_output: str | None = None,
    pidfile_text: str | None = None,
    gateway_dir: str | Path | None = None,
    open_wait_max_s: int = _OPEN_WAIT_MAX_S,
) -> tuple[int, dict[str, Any]]:
    rec: dict[str, Any] = {"t_start": _now_iso()}
    ok, pids = probe_pids(
        expect_stargate_pid=expect_stargate_pid,
        expect_cloud_proxy_pid=expect_cloud_proxy_pid,
        ss_output=ss_output,
        pidfile_text=pidfile_text,
        gateway_dir=gateway_dir,
    )
    rec["pids"] = pids
    if not ok:
        rec["abort"] = "pid_changed_or_restart"
        rec["abort_detail"] = _pid_mismatch_abort_detail(
            pids,
            expect_stargate_pid=expect_stargate_pid,
            expect_cloud_proxy_pid=expect_cloud_proxy_pid,
        )
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
    return 0, rec


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
    gateway_dir: str | Path | None = None,
    sleep_after: Callable[[float], None] = time.sleep,
    open_wait_max_s: int = _OPEN_WAIT_MAX_S,
    stream: bool = False,
) -> tuple[int, dict[str, Any]]:
    read_fn = read_fn or read_status
    rec: dict[str, Any] = {"mode": mode, "t_start": _now_iso()}
    code, guard = preflight_guard(
        expect_stargate_pid=expect_stargate_pid,
        expect_cloud_proxy_pid=expect_cloud_proxy_pid,
        read_fn=read_fn,
        ss_output=ss_output,
        pidfile_text=pidfile_text,
        gateway_dir=gateway_dir,
        open_wait_max_s=open_wait_max_s,
    )
    rec.update(guard)
    if code != 0:
        return code, rec

    st = read_fn()
    ledger_path = ledger_path_from_snapshot(st)
    rec["ledger_path"] = str(ledger_path)
    import sqlite3

    try:
        m0 = ledger_max_id(ledger_path)
    except (OSError, sqlite3.Error):
        m0 = 0
    rec["ledger_max_id_pre"] = m0

    state_before = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    turn_before = int(state_before.get("turn_count", 0))

    cli_exit = 0
    session_line: dict[str, Any] = {}
    if mode == "turn":
        cli_exit, session_line = run_turn(
            session_dir,
            challenge_file=challenge_file,
            read_fn=read_fn,
            stream=stream,
        )
    elif mode == "close":
        if not stop_reason:
            raise ValueError("close requires stop_reason")
        cli_exit, session_line = run_close(
            session_dir,
            stop_reason=stop_reason,
            read_fn=read_fn,
            stream=stream,
        )
    else:
        raise ValueError(f"unknown mode {mode!r}")

    rec["cli_exit"] = cli_exit
    rec["session_line"] = session_line

    send_skipped = bool(session_line.get("send_skipped")) or cli_exit == 3
    if send_skipped:
        next_turn = turn_before + 1 if mode == "turn" else turn_before
        stamp = _now_iso().replace(":", "").replace("+", "")
        abort_path = session_dir / f"turn-{next_turn:02d}-abort-{stamp}-ledger.json"
        rec["abort_ledger_path"] = str(abort_path)
        rec["t_end"] = _now_iso()
        abort_path.write_text(json.dumps(rec, indent=2, default=str) + "\n", encoding="utf-8")
        summary = {"cli_exit": cli_exit, "cli": json.dumps(session_line, default=str)[-600:]}
        return cli_exit, summary

    sleep_after(1)
    new_entries = ledger_entries_after(ledger_path, m0)
    rec["new_entries"] = new_entries

    state = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    n = int(state.get("turn_count", 0))
    rec["turn"] = n
    tf = session_dir / f"turn-{n:02d}.json"
    billed: Decimal | None = None
    send_ok = False
    if tf.exists():
        td = json.loads(tf.read_text(encoding="utf-8"))
        billed = Decimal(str(td.get("billed_usd", "0")))
        send_ok = bool(td.get("send_ok"))
        rec["harness_billed"] = str(billed)
        rec["usage"] = td.get("usage")
        rec["stop_reason_api"] = td.get("stop_reason")

    attr, driver_stop = attribute_ledger_window(new_entries, harness_billed=billed)
    rec.update(attr)
    if send_ok and billed is not None and billed > 0 and not rec.get("our_request_ids"):
        driver_stop = "ledger_attribution_missing"
        rec["driver_stop"] = driver_stop

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
    if driver_stop == "ledger_attribution_missing":
        return 8, summary
    if driver_stop:
        return 7, summary
    return cli_exit if cli_exit != 0 else 0, summary


def _add_session_args(sp: argparse.ArgumentParser) -> None:
    sp.add_argument("--session", type=Path, required=True)
    sp.add_argument("--expect-stargate-pid", required=True)
    sp.add_argument("--expect-cloud-proxy-pid", required=True)
    sp.add_argument(
        "--stream",
        action="store_true",
        help="Use SSE streaming (S1 deviation; floors at gate until FLOOR-FIX F1).",
    )
    sp.add_argument("--gateway-dir", default=None)


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
        stream=bool(getattr(args, "stream", False)),
        gateway_dir=getattr(args, "gateway_dir", None),
    )
    print(json.dumps(summary, default=str))
    return code


if __name__ == "__main__":
    sys.exit(main())
