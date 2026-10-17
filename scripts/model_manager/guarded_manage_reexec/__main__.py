"""CLI entry for guarded manage reexec; default ``--dry-run`` never quits manage.

Operators authorize ``--execute`` only after reading dry-run JSON. Prints one
structured result object on stdout for codeblind disposition.
"""

from __future__ import annotations

import sys

# Real stdout for the JSON result; incidental prints (e.g. workspace validation
# during deploy_identity import) must not land on stdout for jq callers.
_JSON_STDOUT = sys.stdout


def _emit_json(payload: str) -> None:
    print(payload, file=_JSON_STDOUT, flush=True)


if __name__ == "__main__":
    sys.stdout = sys.stderr

import argparse
import json
import subprocess
import traceback
from pathlib import Path

from .deferred_execute import (
    DEFAULT_WAIT_PARENT_TIMEOUT_S,
    DeferredRecycleContext,
    run_deferred_recycle,
)
from .detach import RESULT_PATH, launch_detach
from .result import GuardedReexecResult
from .runner import _write_result_file, run_guarded_reexec


def _resolve_target_ref(explicit: str | None, repo_root: Path) -> str:
    if explicit:
        return explicit
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit(
            "error: --target-ref required (git rev-parse HEAD failed: "
            f"{proc.stderr.strip()})"
        )
    return proc.stdout.strip()


def _exit_for_result(result_status: str, *, checks_refused: bool = False) -> int:
    if result_status == "proof-satisfied":
        return 0
    if result_status == "dry-run" and not checks_refused:
        return 0
    if result_status == "dry-run" and checks_refused:
        return 2
    if result_status == "refused":
        return 2
    if result_status == "proof-failed":
        return 3
    if result_status == "start-failed":
        return 4
    if result_status == "crashed":
        return 1
    return 1


def main(argv: list[str] | None = None) -> int:
    """Parse CLI, run guarded reexec (default dry-run), print JSON result."""
    parser = argparse.ArgumentParser(
        description=(
            "External guarded manage quit/start. Default is --dry-run "
            "(checks only; never quits). Propagate service=manage calls "
            "run_guarded_reexec; this CLI is the same external path."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Perform every check and stop before quit (default)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually pause/quit/reexec/resume (operator-authorized only)",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Launch detached systemd user service (never quit in this process)",
    )
    parser.add_argument(
        "--recover",
        action="store_true",
        help="Detached execute only: enable failure recovery hooks",
    )
    parser.add_argument(
        "--result-path",
        default=None,
        help="Detached execute: JSON result file path",
    )
    parser.add_argument(
        "--target-ref",
        default=None,
        help="Expected post-boot whoami.code_version (default: git HEAD)",
    )
    parser.add_argument(
        "--tmux-target",
        default="0:0",
        help="tmux pane hosting ./manage (default 0:0)",
    )
    parser.add_argument(
        "--intent-db",
        default=None,
        help="Override path to restart-intents.db",
    )
    parser.add_argument(
        "--max-start-attempts",
        type=int,
        default=3,
        help="Bounded start-leg retries after quit (default: 3)",
    )
    parser.add_argument(
        "--parent-dispatch-id",
        default=None,
        help="Detached child: wait until this dispatch is terminal before execute",
    )
    parser.add_argument(
        "--report-thread",
        default=None,
        help="Detached child: agent-bus thread for exactly one maestro reply",
    )
    parser.add_argument(
        "--report-after-turn",
        type=int,
        default=None,
        help="Detached child: --after-turn for the maestro reply",
    )
    parser.add_argument(
        "--wait-parent-timeout-s",
        type=int,
        default=int(DEFAULT_WAIT_PARENT_TIMEOUT_S),
        help="Detached child: max seconds waiting for parent terminal + GIW idle",
    )
    args = parser.parse_args(argv)

    if args.detach and args.execute:
        _emit_json(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "detach_and_execute_mutually_exclusive",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    repo_root = Path(__file__).resolve().parents[3]
    target_ref = _resolve_target_ref(args.target_ref, repo_root)

    if args.detach:
        code, payload = launch_detach(
            target_ref=target_ref,
            tmux_target=args.tmux_target,
            parent_dispatch_id=args.parent_dispatch_id,
            report_thread=args.report_thread,
            report_after_turn=args.report_after_turn,
            wait_parent_timeout_s=args.wait_parent_timeout_s,
        )
        _emit_json(json.dumps(payload, indent=2, sort_keys=True))
        return code

    if args.parent_dispatch_id and not args.execute:
        _emit_json(
            json.dumps(
                {
                    "status": "refused",
                    "reason": "parent_dispatch_requires_execute",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    dry_run = not args.execute
    result_path = Path(args.result_path) if args.result_path else None
    if args.recover and not args.execute:
        _emit_json(
            json.dumps(
                {"status": "refused", "reason": "recover_requires_execute"},
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    effective_result_path = result_path or (RESULT_PATH if args.recover else None)

    if args.execute and args.parent_dispatch_id:
        if not args.report_thread or args.report_after_turn is None:
            _emit_json(
                json.dumps(
                    {
                        "status": "refused",
                        "reason": "deferred_recycle_missing_report_target",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 2
        ctx = DeferredRecycleContext(
            parent_dispatch_id=args.parent_dispatch_id,
            report_thread=str(args.report_thread),
            report_after_turn=int(args.report_after_turn),
            target_ref=target_ref,
            tmux_target=args.tmux_target,
            result_path=effective_result_path or RESULT_PATH,
            wait_timeout_s=float(args.wait_parent_timeout_s),
            repo_root=repo_root,
        )
        exit_code, result = run_deferred_recycle(ctx)
        _emit_json(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return exit_code

    result: GuardedReexecResult | None = None
    exit_code = 1
    try:
        result = run_guarded_reexec(
            target_ref=target_ref,
            dry_run=dry_run,
            tmux_target=args.tmux_target,
            repo_root=repo_root,
            intent_db=Path(args.intent_db) if args.intent_db else None,
            max_start_attempts=args.max_start_attempts,
            recover=args.recover,
            result_path=effective_result_path,
        )
        exit_code = _exit_for_result(
            result.status,
            checks_refused=bool(result.checks.get("refused")),
        )
    except Exception as exc:
        result = GuardedReexecResult(
            status="crashed",
            reason=str(exc),
            dry_run=False,
            target_ref=target_ref,
            executed=False,
            traceback=traceback.format_exc(),
        )
        exit_code = 1
    if effective_result_path is not None and result is not None:
        _write_result_file(effective_result_path, result)
    _emit_json(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
