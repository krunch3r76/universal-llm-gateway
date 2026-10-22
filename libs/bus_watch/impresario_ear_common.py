"""Shared impresario-ear enqueue, hop watermark, echo guard, Grok mailboxes."""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

from bus_watch.fable_lock import WATCH_DIR

log = logging.getLogger("bus_watch.impresario_ear")

PIPELINE_ID = "impresario-ear-v1"
CAPABILITY_PATH = "/api/v1/capabilities/impresario_ear/impresario-ear-v1"
CALLER_AGENT = "agent-bus:impresario-ear"
DISPATCH_TIMEOUT_S = 5.0
_EAR_PREFIX = "EAR |"
_IMPRESARIO_FROM = "impresario-ear"
_GROK_MAILBOXES = frozenset({"grok-bot", "insyncerator"})

_watermark_lock = threading.Lock()
_watermark_path_override: Path | None = None


def watermark_path() -> Path:
    if _watermark_path_override is not None:
        return _watermark_path_override
    root = Path(os.environ.get("IMPRESARIO_EAR_WATERMARK_DIR", WATCH_DIR))
    root.mkdir(parents=True, exist_ok=True)
    return root / "impresario-ear-watermark.json"


def set_watermark_path_for_tests(path: Path | None) -> None:
    global _watermark_path_override
    _watermark_path_override = path


def hop_watermark_key(
    root_thread: str,
    *,
    departing_transcript_id: str | None = None,
    hop_turn: int | None = None,
) -> str:
    root = str(root_thread or "").strip()
    dep = str(departing_transcript_id or "").strip()
    if dep:
        return f"{root}:{dep}"
    if hop_turn is not None:
        return f"{root}:turn:{int(hop_turn)}"
    return root


def _load_watermarks() -> set[str]:
    path = watermark_path()
    if not path.is_file():
        return set()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    keys = raw.get("keys") if isinstance(raw, dict) else raw
    if isinstance(keys, list):
        return {str(k) for k in keys if k}
    return set()


def _save_watermarks(keys: set[str]) -> None:
    path = watermark_path()
    path.write_text(
        json.dumps({"keys": sorted(keys)}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def try_claim_hop_watermark(key: str) -> bool:
    """Return True when this hop watermark was not already enqueued."""
    token = str(key or "").strip()
    if not token:
        return False
    with _watermark_lock:
        keys = _load_watermarks()
        if token in keys:
            return False
        keys.add(token)
        _save_watermarks(keys)
        return True


def hop_watermark_present(key: str) -> bool:
    token = str(key or "").strip()
    if not token:
        return False
    return token in _load_watermarks()


def clear_hop_watermarks_for_tests() -> None:
    with _watermark_lock:
        path = watermark_path()
        if path.is_file():
            path.unlink(missing_ok=True)


def normalize_grok_to_agent(raw: str | None) -> str | None:
    """Map gc1/:9 → grok-bot, gc2/:10 → insyncerator; never grok-bot-gc1 slugs."""
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in _GROK_MAILBOXES:
        return text
    if text in {"grok-bot-gc1", "gc1", ":9"} or "gc1" in text:
        return "grok-bot"
    if text in {"grok-bot-gc2", "gc2", ":10", "insyncerator"} or "gc2" in text:
        return "insyncerator"
    if text.startswith("grok-bot"):
        return "grok-bot"
    return None


def is_ear_turn(turn: dict[str, Any]) -> bool:
    subject = str(turn.get("subject") or "")
    sender = str(
        turn.get("from") or turn.get("from_agent") or turn.get("role") or ""
    ).strip().lower()
    return sender == _IMPRESARIO_FROM or subject.startswith(_EAR_PREFIX)


def ear_echo_should_skip(
    *,
    root_thread: str,
    force: bool,
    fetch_turns: Any,
    last_assistant_hash: str | None = None,
) -> bool:
    """Skip when the tip is already EAR | with no newer assistant activity."""
    if force:
        return False
    listing = fetch_turns(thread=root_thread, last=15) if fetch_turns else {}
    turns = listing.get("turns") if isinstance(listing, dict) else None
    if not isinstance(turns, list):
        return False
    for turn in turns:
        if not isinstance(turn, dict):
            continue
        if is_ear_turn(turn):
            return True
        sender = str(
            turn.get("from") or turn.get("from_agent") or turn.get("role") or ""
        )
        if sender and sender not in {_IMPRESARIO_FROM, "impresario-ear"}:
            break
    del last_assistant_hash  # reserved for JSONL tie-break extensions
    return False


def build_pipeline_options(
    *,
    root_thread: str,
    mode: str = "deep",
    trigger: str = "on_demand",
    dry_run: bool = False,
    force: bool = False,
    departing_transcript_id: str | None = None,
    hop_turn: int | None = None,
    to_agent: str | None = None,
    model: str | None = None,
    patrol_prescreen_result: dict | None = None,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "root": root_thread,
        "root_thread": root_thread,
        "mode": mode,
        "trigger": trigger,
        "dry_run": bool(dry_run),
        "force": bool(force),
    }
    if departing_transcript_id:
        opts["departing_transcript_id"] = departing_transcript_id
    if hop_turn is not None:
        opts["hop_turn"] = int(hop_turn)
    if to_agent:
        opts["to_agent"] = to_agent
    if model:
        opts["model"] = model
    if isinstance(patrol_prescreen_result, dict):
        opts["patrol_prescreen_result"] = patrol_prescreen_result
    return opts


def enqueue_impresario_ear(
    pipeline_options: dict[str, Any],
    *,
    caller_agent: str = CALLER_AGENT,
    blocking: bool = False,
) -> str | None:
    """POST capability pipeline; non-blocking by default (daemon thread when async)."""
    root = str(
        pipeline_options.get("root_thread")
        or pipeline_options.get("root")
        or ""
    ).strip()
    if not root:
        log.warning("impresario ear enqueue refused: missing root")
        return None

    body = {
        "model": PIPELINE_ID,
        "messages": [
            {
                "role": "user",
                "content": f"impresario ear {root} trigger={pipeline_options.get('trigger')}",
            }
        ],
        "pipeline_options": pipeline_options,
        "dispatch_thread_id": root,
        "caller_agent": caller_agent,
    }

    def _post() -> str | None:
        try:
            with make_sync_client(
                DEFAULT_STARGATE_URL, timeout=DISPATCH_TIMEOUT_S
            ) as client:
                resp = client.post(CAPABILITY_PATH, json=body)
            if resp.status_code not in (200, 202):
                log.warning(
                    "impresario ear enqueue http_%s root=%s",
                    resp.status_code,
                    root,
                )
                return None
            payload = resp.json() if resp.content else {}
            execution_id = payload.get("execution_id")
            log.info(
                "impresario ear enqueued root=%s execution_id=%s",
                root,
                execution_id,
            )
            return str(execution_id) if execution_id else None
        except Exception:  # noqa: BLE001
            log.warning("impresario ear enqueue failed root=%s", root, exc_info=True)
            return None

    if blocking:
        return _post()
    thread = threading.Thread(
        target=_post,
        name=f"impresario-ear-{root[:8]}",
        daemon=True,
    )
    thread.start()
    return None


def maybe_enqueue_after_hop(
    *,
    root_thread: str,
    departing_transcript_id: str | None,
    hop_turn: int | None = None,
    channel: str | None = None,
    seal_refused: bool = False,
    replayed: bool = False,
    duplicate: bool = False,
    to_agent: str | None = None,
) -> bool:
    if seal_refused or replayed or duplicate:
        return False
    if channel and channel != "hop":
        return False
    mailbox = normalize_grok_to_agent(to_agent) if to_agent else None
    if mailbox is None and to_agent:
        mailbox = str(to_agent).strip() or None
    if mailbox is None:
        mailbox = "cursor"
    enqueue_impresario_ear(
        build_pipeline_options(
            root_thread=root_thread,
            mode="deep",
            trigger="hop",
            departing_transcript_id=departing_transcript_id,
            hop_turn=hop_turn,
            to_agent=mailbox,
        )
    )
    return True


__all__ = [
    "CAPABILITY_PATH",
    "CALLER_AGENT",
    "PIPELINE_ID",
    "build_pipeline_options",
    "clear_hop_watermarks_for_tests",
    "ear_echo_should_skip",
    "enqueue_impresario_ear",
    "hop_watermark_key",
    "hop_watermark_present",
    "is_ear_turn",
    "maybe_enqueue_after_hop",
    "normalize_grok_to_agent",
    "set_watermark_path_for_tests",
    "try_claim_hop_watermark",
    "watermark_path",
]
