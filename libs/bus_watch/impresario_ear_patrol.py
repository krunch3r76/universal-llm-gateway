"""Zero-token impresario ear patrol — pre-screen subscribed houses."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from bus_watch.fable_lock import WATCH_DIR
from bus_watch.impresario_ear_common import (
    build_pipeline_options,
    enqueue_impresario_ear,
    is_ear_turn,
)

log = logging.getLogger("bus_watch.impresario_ear_patrol")

PATROL_INTERVAL_S = float(os.environ.get("IMPRESARIO_EAR_PATROL_INTERVAL_S", "90"))
PATROL_COOLDOWN_S = float(os.environ.get("IMPRESARIO_EAR_PATROL_COOLDOWN_S", "600"))
_ACTIVITY_GRACE_S = 45.0
_TOOL_MUTATION_RE = re.compile(
    r"team_dispatch|(?:CallDynamicTool.*?(team_dispatch|pipeline|agent_bus))"
    r"|(?:toolName[\"']?\s*:\s*[\"'](team_dispatch|pipeline|agent_bus))",
    re.I,
)

_last_pass: dict[str, float] = {}
_cooldown: dict[str, float] = {}
_cooldown_path_override: Path | None = None


def patrol_cooldown_path() -> Path:
    if _cooldown_path_override is not None:
        return _cooldown_path_override
    root = Path(os.environ.get("IMPRESARIO_EAR_PATROL_DIR", WATCH_DIR))
    root.mkdir(parents=True, exist_ok=True)
    return root / "impresario-ear-patrol-cooldown.json"


def set_patrol_cooldown_path_for_tests(path: Path | None) -> None:
    global _cooldown_path_override
    _cooldown_path_override = path


def _fingerprint(root_id: str, transcript_id: str, assistant_hash: str) -> str:
    return f"{root_id}|{transcript_id}|{assistant_hash}"


def _load_cooldown_file() -> dict[str, float]:
    path = patrol_cooldown_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict):
        return {str(k): float(v) for k, v in raw.items()}
    return {}


def _save_cooldown_file(data: dict[str, float]) -> None:
    patrol_cooldown_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


def patrol_cooldown_active(fp: str, *, now: float | None = None) -> bool:
    clock = now if now is not None else time.time()
    stamp = _cooldown.get(fp)
    if stamp is None:
        file_data = _load_cooldown_file()
        stamp = file_data.get(fp)
    if stamp is None:
        return False
    return (clock - float(stamp)) < PATROL_COOLDOWN_S


def stamp_patrol_cooldown(fp: str, *, now: float | None = None) -> None:
    clock = now if now is not None else time.time()
    _cooldown[fp] = clock
    file_data = _load_cooldown_file()
    file_data[fp] = clock
    _save_cooldown_file(file_data)


def clear_patrol_state_for_tests() -> None:
    _last_pass.clear()
    _cooldown.clear()
    path = patrol_cooldown_path()
    if path.is_file():
        path.unlink(missing_ok=True)


def _assistant_tail_hash(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        role = str(msg.get("role") or msg.get("from_agent") or "")
        if role in {"assistant", "cursor", "impresario-ear"}:
            text = str(msg.get("content") or msg.get("body") or "")
            return hashlib.sha256(text.encode()).hexdigest()
    return ""


def _promised_act_not_fired(
    tail: str, *, in_flight: int, tool_blob: str
) -> bool:
    if in_flight > 0:
        return False
    if _TOOL_MUTATION_RE.search(tool_blob):
        return False
    lowered = tail.lower()
    future = (
        "where this is going" in lowered
        or "who carries it" in lowered
        or "report again" in lowered
    )
    return future and "team_dispatch" not in tool_blob


def prescreen_house(
    *,
    root_id: str,
    transcript_id: str,
    glass_status: str = "completed",
    activity_age_s: float = 999.0,
    in_flight: int = 0,
    assistant_tail: str = "",
    tool_blob: str = "",
    tick: dict[str, Any] | None = None,
    hop_failed: bool = False,
    unmerged_child: bool = False,
    recent_turns: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    signals: list[str] = []
    if glass_status == "generating":
        return {"skip_model": True, "signals": signals, "reason": "generating"}
    if activity_age_s < _ACTIVITY_GRACE_S:
        return {"skip_model": True, "signals": signals, "reason": "activity_grace"}
    if in_flight > 0 and not unmerged_child:
        return {"skip_model": True, "signals": signals, "reason": "in_flight"}
    tail = assistant_tail
    if _promised_act_not_fired(tail, in_flight=in_flight, tool_blob=tool_blob):
        signals.append("promised_act_not_fired")
    if unmerged_child and in_flight == 0:
        signals.append("unharvested_or_unlanded_child")
    if hop_failed:
        signals.append("hop_failed_stranded")
    tick_row = str((tick or {}).get("now_row") or "")
    if tick_row == "quiet" and (unmerged_child or signals):
        signals.append("false_quiet_with_pending_work")
    if recent_turns:
        for turn in recent_turns:
            if is_ear_turn(turn):
                return {"skip_model": True, "signals": signals, "reason": "ear_echo"}
    assistant_hash = hashlib.sha256(tail.encode()).hexdigest() if tail else ""
    fp = _fingerprint(root_id, transcript_id, assistant_hash)
    if patrol_cooldown_active(fp):
        return {"skip_model": True, "signals": signals, "reason": "cooldown", "fingerprint": fp}
    imperative = ""
    if "7139b60" in tail or unmerged_child:
        imperative = "team_dispatch review lane commit 7139b60 — do not stop at narration"
    return {
        "skip_model": len(signals) == 0,
        "signals": signals,
        "fingerprint": fp,
        "imperative": imperative,
        "mode": "patrol" if signals else "deep",
    }


def run_patrol_pass(
    store: Any,
    *,
    now: float | None = None,
    admit_pipeline: Any | None = None,
) -> dict[str, Any]:
    """Scan cursor + grok subscriptions and enqueue patrol/deep runs (non-blocking)."""
    clock = now if now is not None else time.time()
    admit = admit_pipeline or enqueue_impresario_ear
    stats = {"scanned": 0, "enqueued": 0, "skipped_interval": 0}
    agents = ("cursor", "grok-bot", "insyncerator")
    for agent in agents:
        try:
            rows = store.subscriptions(agent=agent)
        except Exception:  # noqa: BLE001
            log.debug("patrol subscriptions failed agent=%s", agent, exc_info=True)
            continue
        for row in rows or []:
            if str(row.get("surface") or "") not in {"cursor", "grok"}:
                continue
            root_id = str(row.get("thread_scope") or row.get("thread_id") or "")
            if not root_id or root_id == "*":
                continue
            stats["scanned"] += 1
            last = _last_pass.get(root_id, 0.0)
            if (clock - last) < PATROL_INTERVAL_S:
                stats["skipped_interval"] += 1
                continue
            _last_pass[root_id] = clock
            transcript_id = str(row.get("agent_id") or root_id)
            pre = prescreen_house(
                root_id=root_id,
                transcript_id=transcript_id,
                glass_status=str(row.get("glass_status") or "completed"),
            )
            if pre.get("skip_model") and not pre.get("signals"):
                continue
            fp = str(pre.get("fingerprint") or "")
            if fp:
                stamp_patrol_cooldown(fp, now=clock)
            mode = str(pre.get("mode") or "patrol")
            admit(
                build_pipeline_options(
                    root_thread=root_id,
                    mode=mode,
                    trigger="patrol",
                ),
                blocking=False,
            )
            stats["enqueued"] += 1
    return stats


async def patrol_prescreen_for_evaluate(
    *, resolve: dict[str, Any], gather: dict[str, Any]
) -> dict[str, Any]:
    if str(resolve.get("mode")) != "patrol":
        return {"skip_model": False}
    jsonl = gather.get("jsonl") if isinstance(gather, dict) else {}
    status = str((jsonl or {}).get("glass_status") or "completed")
    if status == "generating":
        return {"skip_model": True, "verdict": "healthy", "reason": "generating"}
    return {"skip_model": False}


__all__ = [
    "clear_patrol_state_for_tests",
    "patrol_cooldown_active",
    "patrol_prescreen_for_evaluate",
    "prescreen_house",
    "run_patrol_pass",
    "stamp_patrol_cooldown",
]
