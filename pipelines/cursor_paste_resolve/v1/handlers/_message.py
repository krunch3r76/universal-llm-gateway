"""Pure option parse and message assembly for cursor-paste-resolve compose."""

from __future__ import annotations

from typing import Any

ALLOWED_KINDS = frozenset({"friction", "assertion"})
ALLOWED_WINDOWS = frozenset({"glass", "ide"})
ALLOWED_HOSTS = frozenset({"orion-node", "jupiter"})
LAUNCH_TARGETS = frozenset({"glass", "ide", "cursor_sdk"})
MAESTRO_MEMO_THREAD = "12286"
IMPLEMENTER_URI = "cortex://notes/system/prompts/work-item-implementer-friction.md"
IMPLEMENTER_REL = "notes/system/prompts/work-item-implementer-friction.md"


def message_relpath(kind: str, assertion_id: int) -> str:
    return f"tmp/prompts/cursor-paste-{kind}-{assertion_id}.md"


def parse_compose_options(opts: dict[str, Any]) -> dict[str, Any] | str:
    """Return bound fields or an error string."""
    kind = str(opts.get("kind") or "").strip()
    if kind not in ALLOWED_KINDS:
        return "kind must be friction or assertion"
    raw_id = opts.get("assertion_id")
    if isinstance(raw_id, str) and raw_id.isdigit():
        raw_id = int(raw_id)
    if not isinstance(raw_id, int) or raw_id < 1:
        return "assertion_id must be a positive integer"
    window = str(opts.get("window") or "glass").strip()
    if window not in ALLOWED_WINDOWS:
        return "window must be glass or ide"
    notify_raw = str(opts.get("notify") or "").strip()
    notify = "maestro" if notify_raw == "maestro" else ""
    host = str(opts.get("host") or "").strip()
    if host and host not in ALLOWED_HOSTS:
        return "host must be orion-node or jupiter"
    launch_target = str(opts.get("launch_target") or "").strip()
    return {
        "kind": kind,
        "assertion_id": raw_id,
        "window": window,
        "host": host,
        "notify": notify,
        "launch_target": launch_target,
        "dispatch_thread_id": str(opts.get("dispatch_thread_id") or "").strip(),
    }


def rename_block(kind: str, assertion_id: int, closed_suffix: str) -> str:
    return (
        f"Rename this chat tab to {kind}:{assertion_id} · initial.\n"
        f"\n"
        f"You are resolving {kind}:{assertion_id}.\n"
        f"\n"
        f"Keep the title base {kind}:{assertion_id}. Append exactly one stage "
        f"suffix and replace it when the stage changes (do not stack suffixes):\n"
        f"\n"
        f"| Suffix | When |\n"
        f"|---|---|\n"
        f"| initial | first rename; until review or close |\n"
        f"| review | while verifying the fix |\n"
        f"| {closed_suffix} | after the item is closed |\n"
        f"\n"
    )


def maestro_block(kind: str) -> str:
    return (
        f"When this {kind} is closed, inform maestro: send a memo on "
        f"agent-bus thread {MAESTRO_MEMO_THREAD} reporting the closure "
        f"(kind, id, and outcome). Do this after closure, before you stop.\n"
        f"\n"
        f"Thread {MAESTRO_MEMO_THREAD} is that closure memo only. Do not pass "
        f"{MAESTRO_MEMO_THREAD} as dispatch_thread_id, do not post the code "
        f"review on it, and do not harvest the review there. Mint the review "
        f"thread the way the work-item prompt says. Glass and the IDE both "
        f"arm a terminal for the harvest watcher.\n"
        f"\n"
    )


def compose_message(kind: str, assertion_id: int, notify: str, implementer: str) -> str:
    closed = "complete-to-maestro" if notify == "maestro" else "complete"
    parts = [rename_block(kind, assertion_id, closed)]
    if notify == "maestro":
        parts.append(maestro_block(kind))
    parts.append(implementer)
    return "".join(parts)


def team_dispatch_admit_shape(
    *,
    kind: str,
    assertion_id: int,
    message_path: str,
    dispatch_thread_id: str,
) -> dict[str, Any]:
    return {
        "op": "generate",
        "seat": "cursor-sdk",
        "lane": "B",
        "contract": "freeform",
        "prompt": message_path,
        "dispatch_thread_id": dispatch_thread_id,
        "work_key": f"{kind}:{assertion_id}",
    }
