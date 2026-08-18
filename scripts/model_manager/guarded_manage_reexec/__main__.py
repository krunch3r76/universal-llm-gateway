"""CLI entry for guarded manage reexec; default ``--dry-run`` never quits manage.

Operators authorize ``--execute`` only after reading dry-run JSON. Prints one
structured result object on stdout for codeblind disposition.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from .runner import run_guarded_reexec


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


def main(argv: list[str] | None = None) -> int:
    """Parse CLI, run guarded reexec (default dry-run), print JSON result."""
    parser = argparse.ArgumentParser(
        description=(
            "External guarded manage quit/start. Default is --dry-run "
            "(checks only; never quits). Not wired into propagate."
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
    args = parser.parse_args(argv)
    dry_run = not args.execute
    repo_root = Path(__file__).resolve().parents[3]
    target_ref = _resolve_target_ref(args.target_ref, repo_root)
    result = run_guarded_reexec(
        target_ref=target_ref,
        dry_run=dry_run,
        tmux_target=args.tmux_target,
        repo_root=repo_root,
        intent_db=Path(args.intent_db) if args.intent_db else None,
        max_start_attempts=args.max_start_attempts,
    )
    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    if result.status == "proof-satisfied":
        return 0
    if result.status == "dry-run" and not result.checks.get("refused"):
        return 0
    if result.status == "dry-run" and result.checks.get("refused"):
        return 2
    if result.status == "refused":
        # Precondition refuse — manage still up; nothing destroyed.
        return 2
    if result.status == "proof-failed":
        return 3
    if result.status == "start-failed":
        # Quit landed; start/health did not — opposite of precondition refuse.
        return 4
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
