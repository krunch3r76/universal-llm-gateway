"""Per-op argument rewrites before the agent_bus unknown-argument gate.

Distinct from ``_ARG_ALIASES`` in ``_shared.py`` (hint-only, global). Rewrites
here are op-scoped so ``mark_read`` keeps its real ``agent`` parameter.
"""

from __future__ import annotations

from typing import Any


def _fetch_after_turn_error() -> dict[str, Any]:
    return {
        "error": (
            "fetch: unsupported argument after_turn — fetch has no read cursor. "
            "Use fetch(last=N) for a trailing window or get(turn_number=…) for "
            "one turn."
        ),
        "reason": "fetch_after_turn_unsupported",
    }


def _reconcile_wait_args(args: dict[str, Any]) -> dict[str, Any] | None:
    has_wait = "wait_seconds" in args
    has_timeout = "timeout_seconds" in args
    has_timeout_s = "timeout_s" in args

    if has_wait and (has_timeout or has_timeout_s):
        return {
            "error": (
                "wait: pass either wait_seconds or timeout_seconds/timeout_s, "
                "not both."
            ),
            "reason": "wait_timeout_conflict",
        }

    if has_wait or not (has_timeout or has_timeout_s):
        return None

    values: list[Any] = []
    if has_timeout:
        values.append(args["timeout_seconds"])
    if has_timeout_s:
        values.append(args["timeout_s"])
    if len({repr(v) for v in values}) > 1:
        return {
            "error": (
                "wait: timeout_seconds and timeout_s disagree; pass one timeout "
                "alias or use wait_seconds."
            ),
            "reason": "wait_timeout_conflict",
        }

    args["wait_seconds"] = values[0]
    args.pop("timeout_seconds", None)
    args.pop("timeout_s", None)
    return None


def _reconcile_mark_read_args(args: dict[str, Any]) -> dict[str, Any] | None:
    if "reader" in args:
        reader = args.pop("reader")
        if "agent" in args and args["agent"] != reader:
            return {
                "error": (
                    "mark_read: reader and agent disagree; pass one reader address."
                ),
                "reason": "mark_read_reader_agent_conflict",
            }
        args.setdefault("agent", reader)

    if "turn_number" in args:
        turn_number = args["turn_number"]
        if isinstance(turn_number, bool) or not isinstance(turn_number, int):
            return None
        if "turn_numbers" in args:
            return {
                "error": (
                    "mark_read: pass either turn_numbers or turn_number, not both."
                ),
                "reason": "mark_read_turn_spec_conflict",
            }
        args["turn_numbers"] = [turn_number]
        del args["turn_number"]

    return None


def reconcile_dispatch_arguments(
    tool: str, parsed: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Apply op-specific argument rewrites; return (args, error) on hard reject."""
    args = dict(parsed)

    if tool == "fetch" and "after_turn" in args:
        return args, _fetch_after_turn_error()

    if tool == "wait":
        err = _reconcile_wait_args(args)
        if err is not None:
            return args, err

    if tool == "mark_read":
        err = _reconcile_mark_read_args(args)
        if err is not None:
            return args, err

    return args, None
