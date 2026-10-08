"""Per-op argument rewrites before the agent_bus unknown-argument gate.

Distinct from ``_ARG_ALIASES`` in ``_shared.py`` (hint-only, global). Rewrites
here are op-scoped so ``mark_read`` keeps its real ``agent`` parameter.
"""

from __future__ import annotations

from typing import Any


def _advisory(msg: str) -> str:
    return msg


def _timeout_values_disagree(a: Any, b: Any) -> bool:
    try:
        return float(a) != float(b)
    except (TypeError, ValueError):
        return a != b


def _reconcile_wait_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
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
        }, advisories

    if args.get("mark_read") is True:
        advisories.append(
            _advisory(
                "wait: canonical pattern is wait(...), then "
                "mark_read(thread, through_turn=<n>, agent=<you>)"
            )
        )

    if has_wait or not (has_timeout or has_timeout_s):
        return None, advisories

    values: list[Any] = []
    if has_timeout:
        values.append(args["timeout_seconds"])
    if has_timeout_s:
        values.append(args["timeout_s"])
    if len(values) > 1 and _timeout_values_disagree(values[0], values[1]):
        return {
            "error": (
                "wait: timeout_seconds and timeout_s disagree; pass one timeout "
                "alias or use wait_seconds."
            ),
            "reason": "wait_timeout_conflict",
        }, advisories

    args["wait_seconds"] = values[0]
    args.pop("timeout_seconds", None)
    args.pop("timeout_s", None)
    if has_timeout or has_timeout_s:
        advisories.append(
            _advisory("wait: remapped timeout_seconds/timeout_s → wait_seconds")
        )
    return None, advisories


def _reconcile_mark_read_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
    if "reader" in args:
        reader = args.pop("reader")
        if "agent" in args and args["agent"] != reader:
            return {
                "error": (
                    "mark_read: reader and agent disagree; pass one reader address."
                ),
                "reason": "mark_read_reader_agent_conflict",
            }, advisories
        args.setdefault("agent", reader)
        advisories.append(_advisory("mark_read: remapped reader → agent"))

    if "turn_number" in args:
        turn_number = args["turn_number"]
        if isinstance(turn_number, bool) or not isinstance(turn_number, int):
            return {
                "error": (
                    "mark_read: turn_number must be a single int; "
                    "use turn_numbers (list[int]) for multiple turns."
                ),
                "reason": "mark_read_turn_number_invalid",
            }, advisories
        if "turn_numbers" in args:
            return {
                "error": (
                    "mark_read: pass either turn_numbers or turn_number, not both."
                ),
                "reason": "mark_read_turn_spec_conflict",
            }, advisories
        args["turn_numbers"] = [turn_number]
        del args["turn_number"]
        advisories.append(_advisory("mark_read: remapped turn_number → turn_numbers"))

    return None, advisories


def _reconcile_get_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
    if "turn" in args and "turn_number" not in args:
        args["turn_number"] = args.pop("turn")
        advisories.append(_advisory("get: remapped turn → turn_number"))
    elif "turn" in args and "turn_number" in args:
        if args["turn"] != args["turn_number"]:
            return {
                "error": "get: turn and turn_number disagree; pass one turn selector.",
                "reason": "get_turn_turn_number_conflict",
            }, advisories
        del args["turn"]
    return None, advisories


def _reconcile_fetch_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
    if "after_turn" in args:
        advisories.append(
            _advisory(
                "fetch: after_turn is wired to GET /turns (forward cursor); "
                "omit last or use get(turn_number=…) for one turn."
            )
        )
    return None, advisories


def _reconcile_threads_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
    if "limit" in args:
        limit = args.pop("limit")
        if "last" in args and args["last"] != limit:
            return {
                "error": "threads: limit and last disagree; pass one page size.",
                "reason": "threads_limit_last_conflict",
            }, advisories
        args.setdefault("last", limit)
        advisories.append(_advisory("threads: remapped limit → last"))
    return None, advisories


def _reconcile_send_args(args: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
    if "idempotency_key" not in args:
        return None, advisories
    key = args["idempotency_key"]
    has_new_slug = args.get("new_slug") is not None
    has_thread = bool(args.get("thread"))
    if has_new_slug and not has_thread:
        return None, advisories
    if has_thread and not has_new_slug:
        args.pop("idempotency_key")
        advisories.append(
            _advisory(
                "send: dropped idempotency_key on continue (thread=) path; "
                "only valid with new_slug."
            )
        )
        return None, advisories
    if not has_new_slug and not has_thread:
        args.pop("idempotency_key")
        advisories.append(
            _advisory("send: dropped idempotency_key without new_slug or thread")
        )
    return None, advisories


def _reconcile_create_thread_args(
    args: dict[str, Any],
) -> tuple[dict[str, Any] | None, list[str]]:
    advisories: list[str] = []
    dropped: list[str] = []
    for key in ("from", "from_agent"):
        if key in args:
            args.pop(key)
            dropped.append(key)
    if dropped:
        advisories.append(
            _advisory(
                "create_thread: dropped "
                + ", ".join(sorted(dropped))
                + " (not accepted on create_thread)"
            )
        )
    return None, advisories


def reconcile_dispatch_arguments(
    tool: str, parsed: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any] | None, list[str]]:
    """Apply op-specific argument rewrites.

    Returns ``(args, error, advisories)``. *error* is set on hard reject;
    successful rewrites and benign drops append human-readable strings to
    *advisories* (merged into ``argument_rewrite_advisory`` on the response).
    """
    args = dict(parsed)
    advisories: list[str] = []
    err: dict[str, Any] | None

    if tool == "wait":
        err, adv = _reconcile_wait_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    if tool == "mark_read":
        err, adv = _reconcile_mark_read_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    if tool == "get":
        err, adv = _reconcile_get_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    if tool == "fetch":
        err, adv = _reconcile_fetch_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    if tool == "threads":
        err, adv = _reconcile_threads_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    if tool == "send":
        err, adv = _reconcile_send_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    if tool == "create_thread":
        err, adv = _reconcile_create_thread_args(args)
        advisories.extend(adv)
        if err is not None:
            return args, err, advisories

    return args, None, advisories


def merge_argument_rewrite_advisory(
    result: Any, advisories: list[str]
) -> Any:
    """Attach ``argument_rewrite_advisory`` when rewrites or drops occurred."""
    if not advisories:
        return result
    if isinstance(result, dict) and "error" in result:
        return result
    advisory = "; ".join(advisories)
    if isinstance(result, dict):
        merged = dict(result)
        merged["argument_rewrite_advisory"] = advisory
        return merged
    if isinstance(result, list):
        return {"turns": result, "argument_rewrite_advisory": advisory}
    return {"result": result, "argument_rewrite_advisory": advisory}
