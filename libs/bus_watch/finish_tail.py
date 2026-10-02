"""Return one wake line from a conductor watcher log.

The IDE tail stays quiet while the conductor posts ordinary turns. It prints
a single line and exits when the log shows a closeout, or when the conductor
reports a stall. A poller that dies before writing a terminal state is a
stall too: otherwise the seat waits on a state file that will never exist.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

_FINISH_PREFIX = "closeout turn="
_STALL_PREFIX = "stall-pop:"
_NON_CLOSEOUT = "non-closeout turn="
_STALL_WORD = re.compile(r"\bstall(?:ed|ing|-pop)?\b", re.I)
_TERMINAL_STATUS = frozenset({"complete", "expired", "stopped", "stalled"})


def wake_line(line: str) -> str | None:
    """The one line the seat should see, or None while the conductor is working.

    A skipped non-closeout turn is a stall report only when its text says stall.
    Other skips stay silent.
    """
    text = line.strip()
    if text.startswith(_FINISH_PREFIX) or text.startswith(_STALL_PREFIX):
        return text
    if _NON_CLOSEOUT in text and _STALL_WORD.search(text):
        return f"{_STALL_PREFIX} {text}"
    return None


def _state_status(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    status = data.get("status")
    return str(status) if status else None


def _last_wake(text: str) -> str | None:
    found: str | None = None
    for line in text.splitlines():
        wake = wake_line(line)
        if wake:
            found = wake
    return found


def _pid_path(state_path: Path) -> Path:
    name = state_path.name
    suffix = ".state.json"
    if name.endswith(suffix):
        return state_path.with_name(f"{name[: -len(suffix)]}.pid")
    return state_path.with_name(f"{state_path.stem}.pid")


def _poller_alive(state_path: Path) -> bool | None:
    """Whether the supervised pid is alive.

    None means there is no pid file yet, so the tail must keep waiting.
    False means the poller is gone and will not write state.
    """
    pid_path = _pid_path(state_path)
    if not pid_path.is_file():
        return None
    raw = pid_path.read_text(encoding="utf-8", errors="replace").strip()
    if not raw.isdigit():
        return None
    try:
        os.kill(int(raw), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _exit_reason(log_path: Path) -> str:
    """Last human line in the poller log, skipping banners and JSON events."""
    if not log_path.is_file():
        return "poller exited before writing state"
    lines = [
        line.strip()
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    for line in reversed(lines):
        if line.startswith(("{", "[DEBUG]", "✅")):
            continue
        if line.startswith(_STALL_PREFIX):
            continue
        return line[:240]
    return "poller exited before writing state"


def follow(*, log_path: Path, state_path: Path, poll_s: float = 0.5) -> int:
    """Block until a finish or stall line, print it, and return 0.

    A terminal state file with no wake line still returns, and names the
    state so the seat is not left hanging on an expired poller. A dead
    poller pid with no terminal state returns 1 and prints the log's
    last error: argparse can SystemExit after start's liveness check,
    and the state file is never created.
    """
    offset = 0
    while True:
        status = _state_status(state_path)
        chunk = ""
        if log_path.is_file():
            data = log_path.read_bytes()
            if len(data) < offset:
                offset = 0
            chunk = data[offset:].decode("utf-8", errors="replace")
            offset = len(data)
        for line in chunk.splitlines():
            wake = wake_line(line)
            if wake:
                print(wake, flush=True)
                return 0
        if status in _TERMINAL_STATUS:
            prior = ""
            if log_path.is_file():
                prior = log_path.read_text(encoding="utf-8", errors="replace")
            wake = _last_wake(prior)
            print(wake or f"{_STALL_PREFIX} watcher_{status}", flush=True)
            return 0
        if _poller_alive(state_path) is False:
            reason = _exit_reason(log_path)
            print(f"{_STALL_PREFIX} watcher_exited {reason}", flush=True)
            return 1
        time.sleep(poll_s)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--poll-s", type=float, default=0.5)
    args = parser.parse_args(argv)
    return follow(log_path=args.log, state_path=args.state, poll_s=args.poll_s)


if __name__ == "__main__":
    raise SystemExit(main())
