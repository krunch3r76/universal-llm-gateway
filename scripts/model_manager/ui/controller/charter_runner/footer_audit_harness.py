#!/usr/bin/env python3
"""Re-runnable P0-AC5 footer audit harness (§D/§G).

Validate worker-authored CHECKPOINT footers on live charter roots since a git
commit. Prints per-row audit table and consecutive-pass counter (target 5/5).
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.model_manager.ui.controller.charter_runner import bus_client
from scripts.model_manager.ui.controller.charter_runner.checkpoint_body import (
    resolve_checkpoint_body,
)
from scripts.model_manager.ui.controller.charter_runner.harvest import (
    completed_windows,
    turn_number,
)
from scripts.model_manager.ui.controller.charter_runner.harvest_footer_gate import (
    footer_field_path,
    is_machine_authored_checkpoint,
)
from scripts.model_manager.ui.controller.charter_runner.window_log import (
    parse_admission_meta,
)


@dataclass(frozen=True)
class AuditRow:
    root: str
    turn: int
    author_seat: str
    window_id: str
    verdict: str
    error_class: str
    straddling: bool = False
    machine_authored: bool = False


def _git_commit_time(commit: str) -> datetime:
    proc = subprocess.run(
        ["git", "-C", str(_repo_root()), "show", "-s", "--format=%cI", commit],
        check=True,
        capture_output=True,
        text=True,
    )
    raw = proc.stdout.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    return datetime.fromisoformat(raw).astimezone(UTC)


def _repo_root() -> str:
    proc = subprocess.run(
        ["git", "-C", str(Path(__file__).resolve()), "rev-parse", "--show-toplevel"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _parse_posted_at(admission: dict[str, Any]) -> datetime | None:
    meta = parse_admission_meta(str(admission.get("body") or ""))
    raw = str(meta.get("posted_at") or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw).astimezone(UTC)
    except ValueError:
        return None


def _author_seat(turn: dict[str, Any]) -> str:
    return str(turn.get("from") or turn.get("from_agent") or "unknown")


def _audit_row_for_checkpoint(
    *,
    root_id: str,
    admission: dict[str, Any],
    checkpoint: dict[str, Any],
    since: datetime,
) -> AuditRow:
    meta = parse_admission_meta(str(admission.get("body") or ""))
    window_index = int(meta.get("window") or 0)
    posted_at = _parse_posted_at(admission)
    straddling = posted_at is not None and posted_at < since
    subject = str(checkpoint.get("subject") or "")
    machine = is_machine_authored_checkpoint(subject)
    body = resolve_checkpoint_body(
        str(checkpoint.get("body") or ""),
        sidecar_uri=(
            checkpoint.get("sidecar_uri")
            if isinstance(checkpoint.get("sidecar_uri"), str)
            else None
        ),
    )
    if machine:
        return AuditRow(
            root=root_id,
            turn=turn_number(checkpoint),
            author_seat=_author_seat(checkpoint),
            window_id=f"charter-{root_id}-w{window_index}",
            verdict="SKIP",
            error_class="machine_authored",
            straddling=straddling,
            machine_authored=True,
        )
    ok, field_path = footer_field_path(body)
    return AuditRow(
        root=root_id,
        turn=turn_number(checkpoint),
        author_seat=_author_seat(checkpoint),
        window_id=f"charter-{root_id}-w{window_index}",
        verdict="PASS" if ok else "FAIL",
        error_class="" if ok else field_path,
        straddling=straddling,
        machine_authored=False,
    )


async def collect_rows(*, since: datetime, root_filter: str | None) -> list[AuditRow]:
    rows: list[AuditRow] = []
    roots = await bus_client.list_enrolled_roots()
    for thread in roots:
        root_id = str(thread.get("id") or thread.get("thread_id") or "")
        if not root_id or (root_filter and root_id != root_filter):
            continue
        turns = await bus_client.fetch_turns(root_id)
        for admission, checkpoint in completed_windows(turns):
            posted_at = _parse_posted_at(admission)
            if posted_at is None or posted_at < since:
                continue
            rows.append(
                _audit_row_for_checkpoint(
                    root_id=root_id,
                    admission=admission,
                    checkpoint=checkpoint,
                    since=since,
                )
            )
    rows.sort(key=lambda row: (row.root, row.turn))
    return rows


def _print_table(rows: list[AuditRow]) -> None:
    print("root\tturn\tauthor_seat\twindow_id\tverdict\terror_class\tstraddling")
    for row in rows:
        print(
            f"{row.root}\t{row.turn}\t{row.author_seat}\t{row.window_id}\t"
            f"{row.verdict}\t{row.error_class}\t{row.straddling}"
        )


def _consecutive_passes(rows: list[AuditRow]) -> tuple[int, list[AuditRow]]:
    worker_rows = [
        row
        for row in rows
        if not row.machine_authored and not row.straddling and row.verdict != "SKIP"
    ]
    streak = 0
    streak_rows: list[AuditRow] = []
    for row in worker_rows:
        if row.verdict == "PASS":
            streak += 1
            streak_rows.append(row)
            if streak >= 5:
                break
        else:
            streak = 0
            streak_rows = []
    return streak, streak_rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-commit",
        required=True,
        help="Git commit sha — audit windows admitted at or after this commit time",
    )
    parser.add_argument("--root", default="", help="Optional single root id filter")
    args = parser.parse_args(argv)

    since = _git_commit_time(args.since_commit)
    rows = asyncio.run(collect_rows(since=since, root_filter=args.root or None))

    print(f"since_commit={args.since_commit} since_time={since.isoformat()}")
    _print_table(rows)

    machine_rows = [row for row in rows if row.machine_authored]
    if machine_rows:
        print("\nmachine_authored (excluded from 5/5 counter):")
        for row in machine_rows:
            print(f"  root={row.root} turn={row.turn} subject_class={row.error_class}")

    straddling = [row for row in rows if row.straddling]
    if straddling:
        print("\nstraddling (admitted before since-commit; excluded from 5/5 counter):")
        for row in straddling:
            print(f"  root={row.root} turn={row.turn} window={row.window_id}")

    streak, streak_rows = _consecutive_passes(rows)
    print(f"\nconsecutive_worker_passes={streak}/5")
    if streak_rows:
        print("streak_rows:")
        for row in streak_rows:
            print(f"  root={row.root} turn={row.turn} window={row.window_id}")

    return 0 if streak >= 5 else 2


if __name__ == "__main__":
    raise SystemExit(main())
