"""Impresario-ear shared helpers: enqueue, hop watermarks, echo guard, Grok routing.

``bus_watch.ide_hop``, patrol, and ``impresario-ear-v1`` import this module.
Deep hop enqueue is decommissioned (K77); patrol uses ``run_patrol_pass`` separately.
``ear_paused`` stops new patrol scans, enqueues, and pipeline steps while the
pause file or ``IMPRESARIO_EAR_PAUSED`` is set. Wake delivery stays up.
"""

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
_pause_path_override: Path | None = None
_PAUSE_TRUTHY = frozenset({"1", "true", "yes", "on"})


def watermark_path() -> Path:
    """Return the JSON file path storing hop-enqueue watermark keys (creates parent dir)."""
    if _watermark_path_override is not None:
        return _watermark_path_override
    root = Path(os.environ.get("IMPRESARIO_EAR_WATERMARK_DIR", WATCH_DIR))
    root.mkdir(parents=True, exist_ok=True)
    return root / "impresario-ear-watermark.json"


def ear_pause_path() -> Path:
    """Return the sentinel file whose presence pauses the ear completely.

    Default is ``tmp/watchers/impresario-ear-paused``. Override the directory
    with ``IMPRESARIO_EAR_PAUSE_DIR``. Callers check this on every patrol pass
    and before any enqueue, so creating or removing the file takes effect
    without another process restart once this code is loaded.
    """
    if _pause_path_override is not None:
        return _pause_path_override
    root = Path(os.environ.get("IMPRESARIO_EAR_PAUSE_DIR", WATCH_DIR))
    return root / "impresario-ear-paused"


def ear_paused() -> bool:
    """Return whether the operator has paused impresario-ear completely.

    The wake dispatcher, ``enqueue_impresario_ear``, and impresario-ear-v1
    steps call this before scanning subscriptions, posting the capability, or
    running a model. A truthy ``IMPRESARIO_EAR_PAUSED`` env or the pause file
    from ``ear_pause_path`` stops that work. Endpoint wake delivery is separate
    and keeps running. Clear the env and remove the file to resume.
    """
    raw = os.environ.get("IMPRESARIO_EAR_PAUSED", "").strip().lower()
    if raw in _PAUSE_TRUTHY:
        return True
    return ear_pause_path().is_file()


def set_pause_path_for_tests(path: Path | None) -> None:
    """Redirect ``ear_pause_path`` to a temp file; ``None`` restores the default."""
    global _pause_path_override
    _pause_path_override = path


def set_watermark_path_for_tests(path: Path | None) -> None:
    """Redirect ``watermark_path()`` to a temp file; ``None`` restores production default."""
    global _watermark_path_override
    _watermark_path_override = path


def hop_watermark_key(
    root_thread: str,
    *,
    departing_transcript_id: str | None = None,
    hop_turn: int | None = None,
) -> str:
    """Stable id for one hop-triggered ear run (root, departing transcript, or turn)."""
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
    """Return whether ``key`` is already recorded in the hop watermark store."""
    token = str(key or "").strip()
    if not token:
        return False
    return token in _load_watermarks()


def clear_hop_watermarks_for_tests() -> None:
    """Remove the watermark JSON file under the current ``watermark_path()`` (tests only)."""
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
    """True when the bus turn is from impresario-ear or carries an ``EAR |`` subject."""
    subject = str(turn.get("subject") or "")
    sender = (
        str(turn.get("from") or turn.get("from_agent") or turn.get("role") or "")
        .strip()
        .lower()
    )
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
    """Build ``pipeline_options`` dict for ``enqueue_impresario_ear`` and the Stargate capability POST body."""
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
    """POST capability pipeline; non-blocking by default (daemon thread when async).

    Returns ``None`` without a POST when ``ear_paused`` is true, so a live
    subscription cannot start an ear run during an operator pause.
    """
    if ear_paused():
        log.info("impresario ear enqueue skipped: paused")
        return None
    root = str(
        pipeline_options.get("root_thread") or pipeline_options.get("root") or ""
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
    """Hop hook after IDE/Grok seal: deep ear decommissioned (K77); patrol unchanged.

    Called from ``ide_hop.finalize_xvfb_hop_sidebar`` and checkpoint ``post`` handlers.
    Returns ``False`` without enqueueing or claiming a hop watermark so a skipped deep
    run cannot burn the watermark. Patrol enqueue remains ``run_patrol_pass`` only.
    """
    if seal_refused or replayed or duplicate:
        return False
    if channel and channel != "hop":
        return False
    return False


__all__ = [
    "CAPABILITY_PATH",
    "CALLER_AGENT",
    "PIPELINE_ID",
    "build_pipeline_options",
    "clear_hop_watermarks_for_tests",
    "ear_echo_should_skip",
    "ear_pause_path",
    "ear_paused",
    "enqueue_impresario_ear",
    "set_pause_path_for_tests",
    "hop_watermark_key",
    "hop_watermark_present",
    "is_ear_turn",
    "maybe_enqueue_after_hop",
    "normalize_grok_to_agent",
    "set_watermark_path_for_tests",
    "try_claim_hop_watermark",
    "watermark_path",
]
