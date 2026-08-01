"""Allow running as `python -m scripts.model_manager.ui`."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence

from scripts.model_manager.ensure_venv import ensure_venv, find_workspace_root

ensure_venv(find_workspace_root())

from scripts.model_manager.ui.controller.service_config import (  # noqa: E402
    bootstrap_manage_process_logging_env,
)

bootstrap_manage_process_logging_env()

_NON_TTY_MSG = (
    "error: manage TUI requires a TTY on stdin; "
    'use manage.sock or MCP manage(action="status"|"whoami"|"busy_status") '
    "for headless status.\n"
)

_USAGE_MSG = (
    "usage: python -m scripts.model_manager.ui\n"
    "error: unexpected arguments; this module launches the interactive TUI only. "
    "For status, use manage.sock / MCP manage.\n"
)


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin_isatty: bool | None = None,
    run_fn: Callable[[], None] | None = None,
    acquire_lock_fn: Callable[[], int] | None = None,
    release_lock_fn: Callable[[int], None] | None = None,
) -> int:
    """Guard then launch the Textual TUI.

    Rejects unexpected argv and non-tty stdin (exit 2) before calling ``run_fn``.
    Then takes the exclusive single-instance lock (exit 3 when another manage
    already holds it) so a second launch never reaches Textual, the charter
    runner, or the digest loop. ``run_fn`` defaults to a lazy import of
    ``app.run`` so hermetic tests can inject a mock without loading Textual.
    """
    extras = list(sys.argv[1:] if argv is None else argv)
    if extras:
        sys.stderr.write(_USAGE_MSG)
        return 2

    is_tty = sys.stdin.isatty() if stdin_isatty is None else stdin_isatty
    if not is_tty:
        sys.stderr.write(_NON_TTY_MSG)
        return 2

    from scripts.model_manager.ui.single_instance import (  # noqa: PLC0415
        ManageAlreadyRunningError,
        acquire_manage_lock,
        release_manage_lock,
    )

    acquire_lock_fn = acquire_lock_fn or acquire_manage_lock
    release_lock_fn = release_lock_fn or release_manage_lock

    try:
        lock_fd = acquire_lock_fn()
    except ManageAlreadyRunningError as exc:
        sys.stderr.write(str(exc))
        return 3

    if run_fn is None:
        from scripts.model_manager.ui.app import run as run_fn  # noqa: PLC0415

    try:
        run_fn()
    finally:
        release_lock_fn(lock_fd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
