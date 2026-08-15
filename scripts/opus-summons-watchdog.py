#!/usr/bin/env python3
"""Auto-summons loop for an operator-proxy arc (commission pointer + idle CSE).

The seat owns continuity *content* (rewrites NEXT-EPISODE-COMMISSION.md at close);
this loop owns only the *summons*. It never forces, aborts, or kills.

**Fast path (2026-08-02):** when ``commission_seq > last_launched`` and
``running_count == 0``, summon immediately — a lingering ``live_cse`` must not
block the successor. Idle-confirm path remains for same-seq reopen after CSE death.

Stops on: arc_complete, non-incrementing commission_seq (replay guard),
episode budget, wall-clock deadline, or two consecutive launch failures.

**Invariant:** every terminal stop pages the operator (awareness / mission-debrief).
Mid-run silence while the operator is asleep is fine; silent stop is not.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

COMMISSION = Path(
    os.environ.get(
        "OPUS_SUMMONS_COMMISSION",
        "/mnt/torus/mcp-data/files/notes/system/threads/"
        "cursor-sdk-feature-alignment/status-must-be-observed/"
        "NEXT-EPISODE-COMMISSION.md",
    )
)
COMMISSION_URI = os.environ.get(
    "OPUS_SUMMONS_COMMISSION_URI",
    "cortex://notes/system/threads/cursor-sdk-feature-alignment/"
    "status-must-be-observed/NEXT-EPISODE-COMMISSION.md",
)
ACTIVE_WORK = os.environ.get(
    "OPUS_SUMMONS_ACTIVE_WORK", "http://jupiter:8770/v1/project-ask/active-work"
)
DRAIN_STATE = os.environ.get(
    "OPUS_SUMMONS_DRAIN_STATE", "http://jupiter:8770/v1/project-ask/drain-state"
)
DISPATCH = os.environ.get(
    "OPUS_SUMMONS_DISPATCH", "http://localhost:9999/api/v1/team/dispatch"
)
LOG = Path(
    os.environ.get(
        "OPUS_SUMMONS_LOG",
        str(Path(__file__).resolve().parents[1] / "tmp/watch/opus-summons.jsonl"),
    )
)
EMAIL_BRIDGE_SOCK = os.environ.get(
    "EMAIL_BRIDGE_SOCK", "/tmp/universal-protocol/email-bridge.sock"
)

THREAD = os.environ.get("OPUS_SUMMONS_THREAD", "6655")
POLL_S = int(os.environ.get("OPUS_SUMMONS_POLL_S", "90"))
IDLE_CONFIRMATIONS = int(os.environ.get("OPUS_SUMMONS_IDLE_CONFIRMATIONS", "3"))
MAX_EPISODES = int(os.environ.get("OPUS_SUMMONS_MAX_EPISODES", "8"))
MAX_FAILURES = int(os.environ.get("OPUS_SUMMONS_MAX_FAILURES", "2"))
ATTACH_GRACE_S = int(os.environ.get("OPUS_SUMMONS_ATTACH_GRACE_S", "420"))
DEADLINE_EPOCH = 0.0


def log(event: str, **fields) -> None:
    """Append one structured watchdog event to disk and mirror it to stdout."""
    rec = {"ts": datetime.now(UTC).isoformat(timespec="seconds"), "event": event}
    rec.update(fields)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as fh:
        fh.write(json.dumps(rec) + "\n")
    print(json.dumps(rec), flush=True)


def _mission_name_from_commission() -> str:
    try:
        text = COMMISSION.read_text()
    except OSError:
        return "cursor-sdk three-fold mission"
    if not text.startswith("---"):
        return "cursor-sdk three-fold mission"
    head = text.split("---", 2)[1]
    mission = ""
    arc = ""
    for line in head.splitlines():
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip().strip("\"'")
        if key == "mission":
            mission = val
        elif key == "arc":
            arc = val
    return mission or arc or "cursor-sdk three-fold mission"


def page_on_stop(reason: str, **fields) -> bool:
    """Page operator on every terminal stop. Fail-open: log and continue exit."""
    if os.environ.get("PAGER_NOTIFY_ENABLED", "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        log("page_skipped", reason="pager_disabled", stop_reason=reason)
        return False

    try:
        from pager_notify.mission_page import (
            SUMMONS_ARCH_ONE_LINER,
            format_summons_stop_page,
            summons_look_ahead,
            summons_look_back,
        )
    except ImportError as exc:
        log("page_failed", reason=reason, error=f"import:{exc}")
        return False

    mission = _mission_name_from_commission()
    look_back = summons_look_back(reason)
    look_ahead = summons_look_ahead(reason)
    detail = " ".join(f"{k}={v}" for k, v in sorted(fields.items()) if v is not None)
    if detail:
        look_back = f"{look_back} ({detail})"

    subject, body, tag = format_summons_stop_page(
        reason=reason,
        mission=mission,
        looking_back=look_back,
        architecture=SUMMONS_ARCH_ONE_LINER,
        looking_ahead=look_ahead,
        beyond_bullets=[
            "Read the live commission finish line — followup: cortex NEXT-EPISODE-COMMISSION",
            "If folds 2/3 still open: continue mission, do not treat stop as done — "
            f"followup: MONITOR watches {THREAD}",
            "Restarts named in closeout — operator_gate: IDE manage when clear",
        ],
    )
    payload = {"subject": subject, "body": body, "tag": tag}
    proc = subprocess.run(
        [
            "curl",
            "-sS",
            "--unix-socket",
            EMAIL_BRIDGE_SOCK,
            "-H",
            "Content-Type: application/json",
            "-d",
            json.dumps(payload),
            "http://localhost/pager/notify",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        log("page_failed", reason=reason, error=proc.stderr.strip()[:300])
        return False
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        log("page_failed", reason=reason, error=f"bad_json:{proc.stdout[:200]!r}")
        return False
    ok = str(data.get("status")) == "sent"
    log("paged", reason=reason, status=data.get("status"), ok=ok)
    return ok


def stop(reason: str, exit_code: int = 0, **fields) -> int:
    """Record a terminal watchdog reason, page the operator, and return its exit code."""
    log("stop", reason=reason, **fields)
    page_on_stop(reason, **fields)
    return exit_code


def curl_json(url: str, payload: dict | None = None, timeout: int = 30) -> dict | None:
    """GET or POST JSON via curl; returns parsed body or None on any failure."""
    cmd = ["curl", "-sS", "--max-time", str(timeout), url]
    if payload is not None:
        cmd += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    except subprocess.TimeoutExpired:
        return None
    if out.returncode != 0:
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def read_commission() -> tuple[int | None, bool]:
    """Parse ``commission_seq`` and ``arc_complete`` from commission YAML front-matter without side effects."""
    try:
        text = COMMISSION.read_text()
    except OSError:
        return None, False
    if not text.startswith("---"):
        return None, False
    head = text.split("---", 2)[1]
    seq: int | None = None
    complete = False
    for line in head.splitlines():
        key, _, val = line.partition(":")
        key, val = key.strip(), val.strip().strip("\"'").lower()
        if key == "commission_seq":
            try:
                seq = int(val)
            except ValueError:
                seq = None
        elif key == "arc_complete":
            complete = val == "true"
    return seq, complete


def lane_counts() -> tuple[int | None, int | None]:
    """Return recorded streams and observed live CSE counts, or unknown values on failure."""
    data = curl_json(ACTIVE_WORK, timeout=10)
    drain = curl_json(DRAIN_STATE, timeout=10)
    if data is None or drain is None:
        return None, None
    return int(data.get("running_count", 0) or 0), int(
        drain.get("live_cse_count", 0) or 0
    )


def try_summon(
    *,
    seq: int | None,
    last_launched_seq: int,
    episodes: int,
    failures: int,
    path: str,
) -> tuple[int, int, int]:
    """Attempt one successor summon and return updated launch, episode, and failure counters."""
    if seq is None:
        raise SystemExit(stop("commission_unreadable", 1, episodes=episodes))
    if seq <= last_launched_seq:
        raise SystemExit(
            stop(
                "commission_seq_not_incremented",
                seq=seq,
                last_launched=last_launched_seq,
                episodes=episodes,
                note="departing seat did not rewrite the pointer; not replaying",
            )
        )
    if episodes >= MAX_EPISODES:
        raise SystemExit(stop("episode_budget", episodes=episodes))

    log("summon_attempt", seq=seq, path=path, last_launched=last_launched_seq)
    exec_id = summon(seq)
    if exec_id and await_attach(exec_id):
        episodes += 1
        last_launched_seq = seq
        log("attached", seq=seq, execution_id=exec_id, episodes=episodes, path=path)
        return last_launched_seq, episodes, 0

    failures += 1
    log("launch_unconfirmed", seq=seq, failures=failures, path=path)
    if failures >= MAX_FAILURES:
        raise SystemExit(stop("consecutive_failures", 1, episodes=episodes))
    return last_launched_seq, episodes, failures


def summon(seq: int) -> str | None:
    """Submit the successor episode. Returns execution_id, or None on failure."""
    body = {
        "op": "generate",
        "dispatch_thread_id": THREAD,
        "contract": "light-bounded",
        "model": "cdp/opus-5",
        "purpose": "operator-proxy",
        "sidecar_ref": COMMISSION_URI,
        "caller_agent": "cursor-summons-watchdog",
    }
    resp = curl_json(DISPATCH, payload=body, timeout=120)
    if resp is None or "error" in resp:
        log("summon_failed", seq=seq, response=resp)
        return None
    exec_id = resp.get("execution_id")
    log("summoned", seq=seq, execution_id=exec_id, status=resp.get("status"))
    return exec_id


def await_attach(exec_id: str | None = None) -> bool:
    """Confirm the summoned episode attached (admission ≠ arrival).

    When ``exec_id`` is set, require that id in active-work so a lingering
    predecessor CSE is not mistaken for the successor.
    """
    want = exec_id.replace("-", "") if exec_id else None
    deadline = time.time() + ATTACH_GRACE_S
    while time.time() < deadline:
        time.sleep(15 if exec_id else 30)
        data = curl_json(ACTIVE_WORK, timeout=10)
        if data is None:
            continue
        if want:
            ids = {str(x).replace("-", "") for x in (data.get("execution_ids") or [])}
            rows = data.get("rows") or []
            row_ids = {
                str(r.get("execution_id") or "").replace("-", "") for r in rows
            }
            if want in ids or want in row_ids:
                return True
            continue
        drain = curl_json(DRAIN_STATE, timeout=10)
        if int(data.get("running_count", 0) or 0) or (
            drain is not None and int(drain.get("live_cse_count", 0) or 0)
        ):
            return True
    return False


def main() -> int:
    """Run the bounded summons loop until the commission or drain policy stops it."""
    global DEADLINE_EPOCH
    if len(sys.argv) < 3:
        print(
            "usage: opus-summons-watchdog.py <deadline_epoch> <in_flight_seq>",
            file=sys.stderr,
        )
        return 2
    DEADLINE_EPOCH = float(sys.argv[1])
    last_launched_seq = int(sys.argv[2])

    episodes = 0
    failures = 0
    idle_streak = 0
    log(
        "armed",
        deadline=datetime.fromtimestamp(DEADLINE_EPOCH, UTC).isoformat(),
        in_flight_seq=last_launched_seq,
        max_episodes=MAX_EPISODES,
    )

    while True:
        if time.time() >= DEADLINE_EPOCH:
            return stop("deadline", episodes=episodes)

        seq, complete = read_commission()
        if complete:
            return stop("arc_complete", seq=seq, episodes=episodes)

        running, live = lane_counts()
        if running is None:
            log("probe_failed")
            idle_streak = 0
            time.sleep(POLL_S)
            continue

        # Fast path (operator 2026-08-02): commission already advanced and no
        # in-flight request work ⇒ summon now. A lingering live CSE must not
        # block the successor — that was the multi-minute stall after ep8.
        successor_pending = seq is not None and seq > last_launched_seq
        if successor_pending and running == 0:
            last_launched_seq, episodes, failures = try_summon(
                seq=seq,
                last_launched_seq=last_launched_seq,
                episodes=episodes,
                failures=failures,
                path="successor_fast",
            )
            idle_streak = 0
            time.sleep(POLL_S)
            continue

        if running or live:
            idle_streak = 0
        else:
            idle_streak += 1

        if idle_streak >= IDLE_CONFIRMATIONS:
            last_launched_seq, episodes, failures = try_summon(
                seq=seq,
                last_launched_seq=last_launched_seq,
                episodes=episodes,
                failures=failures,
                path="idle_confirm",
            )
            idle_streak = 0

        time.sleep(POLL_S)


if __name__ == "__main__":
    raise SystemExit(main())
