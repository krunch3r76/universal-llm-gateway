"""Fail-closed refusal when host-targeting operations run under dispatch HOME.

Host-targeting tools (plugin install, manage reexec python, etc.) must not treat
``$HOME`` / ``Path.home()`` as the operator passwd home when the process runs
inside a cursor-sdk dispatch overlay. Detection reuses
:func:`is_dispatch_home_path` — not a parallel marker heuristic.

Instance 3 (``charter_runner_store.db._operator_home``) performs intentional
silent redirect and is **exempt** — do not call this guard there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from services.git_integration_worker.cursor_home import (
    is_dispatch_home_path,
    operator_real_home,
)


class DispatchHomeHostRefusal(SystemExit):
    """Process HOME is a dispatch overlay; host-targeting operation refused."""


def host_target_refusal_message(*, tool: str, overlay_home: Path) -> str:
    """Human-readable refusal naming the operator-home re-invocation."""
    host = operator_real_home()
    return (
        f"REFUSED: {tool} targets operator-host paths but HOME={overlay_home} "
        "is a cursor-sdk dispatch overlay (under cursor-dispatch-homes).\n"
        "Re-run from operator HOME, e.g.:\n"
        f"  HOME={host} {tool}\n"
        "Or set an explicit host pin (CHARTER_RUNNER_OPERATOR_HOME, "
        "WHAT_IS_RUNNING_HOST_HOME, etc.) where the tool documents one."
    )


def refuse_host_target_if_dispatch_home(
    *,
    tool: str,
    home: Path | str | None = None,
) -> None:
    """Raise :class:`DispatchHomeHostRefusal` when *home* is a dispatch overlay.

    When *home* is omitted, uses ``os.environ['HOME']`` then ``Path.home()``.
    A warning that proceeds is the defect — callers must not catch-and-continue.
    """
    env_raw = os.environ.get("HOME", "").strip()
    candidate = Path(home if home is not None else env_raw or Path.home()).expanduser()
    if not str(candidate):
        return
    if is_dispatch_home_path(candidate):
        raise DispatchHomeHostRefusal(
            host_target_refusal_message(tool=tool, overlay_home=candidate)
        )


def main(argv: list[str] | None = None) -> int:
    """CLI for bash callers: ``python -m …dispatch_home_host_guard <tool-name>``."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) != 1:
        print(
            "usage: python -m services.git_integration_worker.dispatch_home_host_guard "
            "<tool-name>",
            file=sys.stderr,
        )
        return 2
    try:
        refuse_host_target_if_dispatch_home(tool=args[0])
    except DispatchHomeHostRefusal as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
