#!/usr/bin/env python3
"""CLI for lane-B seat write registration (Cursor hooks + manual invoke).

Mechanism: ``afterFileEdit`` / ``sessionStart`` / ``sessionEnd`` hooks call this
script; it writes to ``SeatWriteLedger`` (``DATA_DIR/seat-write-ledger.db``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Repo root on PYTHONPATH via sitecustomize when using universal venv.
from services.git_integration_worker.seat_write_ledger import SeatWriteLedger


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _rel_path(source_repo: Path, file_path: str) -> str | None:
    candidate = Path(file_path).resolve()
    try:
        return candidate.relative_to(source_repo.resolve()).as_posix()
    except ValueError:
        return None


def cmd_open(args: argparse.Namespace) -> int:
    SeatWriteLedger.instance().open_arc(
        arc_id=args.arc,
        seat_id=args.seat,
        source_repo=args.repo,
    )
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    SeatWriteLedger.instance().close_arc(arc_id=args.arc)
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    n = SeatWriteLedger.instance().register_paths(
        arc_id=args.arc,
        seat_id=args.seat,
        source_repo=args.repo,
        paths=tuple(args.paths),
    )
    print(json.dumps({"registered": n, "arc_id": args.arc}))
    return 0


def cmd_hook_session_start(payload: dict) -> int:
    arc_id = str(payload.get("conversation_id") or payload.get("session_id") or "")
    if not arc_id:
        arc_id = f"ide-{payload.get('generation_id', 'unknown')}"
    repo = payload.get("workspace_roots", [str(_repo_root())])
    source = repo[0] if isinstance(repo, list) and repo else str(_repo_root())
    SeatWriteLedger.instance().open_arc(
        arc_id=arc_id,
        seat_id="ide-composer",
        source_repo=source,
    )
    return 0


def cmd_hook_session_end(payload: dict) -> int:
    arc_id = str(payload.get("conversation_id") or payload.get("session_id") or "")
    if arc_id:
        SeatWriteLedger.instance().close_arc(arc_id=arc_id)
    return 0


def cmd_hook_after_file_edit(payload: dict) -> int:
    arc_id = str(payload.get("conversation_id") or payload.get("session_id") or "")
    if not arc_id:
        return 0
    file_path = str(payload.get("file_path") or payload.get("path") or "")
    if not file_path:
        return 0
    repo_roots = payload.get("workspace_roots") or [str(_repo_root())]
    source = repo_roots[0] if isinstance(repo_roots, list) else str(_repo_root())
    rel = _rel_path(Path(source), file_path)
    if rel is None:
        return 0
    SeatWriteLedger.instance().register_paths(
        arc_id=arc_id,
        seat_id="ide-composer",
        source_repo=source,
        paths=(rel,),
    )
    return 0


def cmd_hook(stdin_payload: dict, *, event: str) -> int:
    if event == "sessionStart":
        return cmd_hook_session_start(stdin_payload)
    if event == "sessionEnd":
        return cmd_hook_session_end(stdin_payload)
    if event in {"afterFileEdit", "postToolUse"}:
        return cmd_hook_after_file_edit(stdin_payload)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lane-B seat write registration")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("open", cmd_open),
        ("close", cmd_close),
        ("register", cmd_register),
    ):
        p = sub.add_parser(name)
        p.add_argument("--arc", required=True)
        p.add_argument("--seat", default="ide-composer")
        p.add_argument("--repo", default=str(_repo_root()))
        if name == "register":
            p.add_argument("paths", nargs="+")
        p.set_defaults(func=handler)

    hook_p = sub.add_parser("hook")
    hook_p.add_argument("--event", required=True)
    hook_p.set_defaults(func=lambda args: cmd_hook(json.load(sys.stdin), event=args.event))

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
