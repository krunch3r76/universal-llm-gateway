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

from bus_watch.digest_budget import effective_policy
from bus_watch.fable_lock import WATCH_DIR
from bus_watch.ide_budget import AGENT_TRANSCRIPTS
from bus_watch.impresario_ear_common import (
    build_pipeline_options,
    enqueue_impresario_ear,
    is_ear_turn,
    normalize_grok_to_agent,
)
from bus_watch.roster import _holders_from_active_work, fetch_giw_active_work_payload
from bus_watch.state import read_state

log = logging.getLogger("bus_watch.impresario_ear_patrol")

PATROL_INTERVAL_S = float(os.environ.get("IMPRESARIO_EAR_PATROL_INTERVAL_S", "90"))
PATROL_COOLDOWN_S = float(os.environ.get("IMPRESARIO_EAR_PATROL_COOLDOWN_S", "600"))
_ACTIVITY_GRACE_S = 45.0
_TOOL_MUTATION_RE = re.compile(
    r"^(?!.*GetDynamicTools)(?:"
    r"[^\n]*\bteam_dispatch\b"
    r"|(?=[^\n]*\"toolName\"\s*:\s*\"pipeline\")(?=[^\n]*\"op\"\s*:\s*\"run\")[^\n]+"
    r"|(?=[^\n]*\"toolName\"\s*:\s*\"agent_bus\")(?!.*\"tool\"\s*:\s*\"(?:mark_read|get|fetch|fetch_unread|threads|thread_get|wait)\")[^\n]+"
    r"|(?=[^\n]*\"toolName\"\s*:\s*\"cortex\")(?=[^\n]*\"tool\"\s*:\s*\"(?:friction|assert|entity_create|entity_update)\")[^\n]+"
    r"|[^\n]*liaison-tick\.py[^\n]*--set"
    r"|(?![^\n]*\"toolName\"\s*:\s*\"fs\")[^\n]*\bcortex\s*\([^\n]*(?:friction|assert|entity_create|entity_update)"
    r"|[^\n]*\bpipeline\s*\([^\n]*\bop\s*=\s*[\"']run[\"']"
    r"|[^\n]*\bagent_bus\s*\([^\n]*\btool\s*=\s*[\"'](?:send|reply|post|resolve|request|create_thread|close)[\"']"
    r")",
    re.I | re.M,
)
_ISSUE_CONFIRM_RE = re.compile(
    r"(?i)\b(?:"
    r"confirmed|confirming|stale\s+cp|stale\s+residue|unverified|defect|bug|drift|"
    r"mismatch(?:es)?|overrid(?:e|den)|missed\s+degraded|missed\s+wake|root\s+cause|"
    r"reproduced|died\s+at|has\s+no\s+attribute|does\s+not\s+prove|wrong\s+stop|"
    r"did\s+not\s+stick|does\s+not\s+match|fast=false|checks_failed|"
    r"stated_intent_no_write|provider\s+connection\s+error|unsubscribed"
    r")\b"
)
_PASSIVE_PARK_CLOSE_RE = re.compile(
    r"(?i)(?:"
    r"going\s*[.:—-]\s*stay\s+here|"
    r"\*\*going\.\*\*\s*stay\s+here|"
    r"\bstay\s+here\b|"
    r"\bidle\s+until\s+next\s+wake\b|"
    r"\bidle\s+until\s+(?:the\s+next|a\s+land)\b|"
    r"\bnow\s+is\s+(?:empty|quiet|none)\b[^\n]*\b(?:stay|idle)\b|"
    r"\bthe\s+house\s+stays\s+(?:clear|seated)\b|"
    r"\bi\s+am\s+not\s+admitting\s+a\s+conductor\b|"
    r"\bno\s+timer\s+(?:is\s+)?armed\b|"
    r"\bdid\s+not\s+re-run\s+[^\n]*\btonight\b|"
    r"\bwait(?:ing|s)?\s+(?:on|for)\s+the\s+next\s+push\b"
    r")"
)
_EXPLICIT_ACTION_OFFER_RE = re.compile(
    r"(?i)(?:"
    r"\bi\s+can\s+(?:offer|dispatch|run|update|mint|file|fix|reconcile)\b|"
    r"\bconcrete\s+actions?\s+(?:i\s+can|this\s+seat\s+can|to)\s+(?:offer|take)\b|"
    r"\boptions\s+to\s+proceed\b|"
    r"\bnext_admit\s*:"
    r")"
)
_PASSIVE_ISSUE_IMPERATIVE = (
    "NEXT_ADMIT: state concrete remedial actions for the confirmed operator "
    "issue and execute in-lock (liaison-tick.py --set, live probe, "
    "friction/todo mint, advisor consult, or cursor-sdk lane=B dispatch) — "
    "do not park on Going. Stay here."
)
_HOP_FAILED_RE = re.compile(
    r"(?i)(?:phase=keystroke|hop.{0,48}(?:fail|refus|stranded)|debug_unreachable|"
    r"exit\s*code\s*2|no successor)",
)
_UNMERGED_CHILD_RE = re.compile(
    r"(?i)(?:land_disposition:\s*unlanded|\bunmerged\b|\bnot.?landed\b|"
    r"unharvested_or_unlanded)",
)
_NON_TERMINAL_HOLDER = frozenset(
    {
        "acting",
        "admitted",
        "running",
        "queued",
        "claimed",
        "parked",
        "parked_waiting",
        "in_flight",
        "hopping",
    }
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


def _turns_listing(store: Any, root_id: str, *, last: int = 10) -> list[dict[str, Any]]:
    fetch = getattr(store, "turns", None)
    if not callable(fetch):
        return []
    try:
        listing = fetch(thread=root_id, last=last)
    except Exception:  # noqa: BLE001
        log.debug("patrol turns fetch failed root=%s", root_id, exc_info=True)
        return []
    turns = listing.get("turns") if isinstance(listing, dict) else None
    return [t for t in turns or [] if isinstance(t, dict)]


def _holder_thread(holder: dict[str, Any]) -> str:
    for key in ("thread_id", "dispatch_thread_id", "lane_thread_id", "root_thread"):
        val = holder.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _in_flight_for_root(root_id: str) -> int:
    payload = fetch_giw_active_work_payload()
    if not payload:
        return 0
    count = 0
    for holder in _holders_from_active_work(payload):
        if _holder_thread(holder) != root_id:
            continue
        status = str(holder.get("status") or holder.get("state") or "").lower()
        if not status or status in _NON_TERMINAL_HOLDER:
            count += 1
    return count


def _transcript_probe(
    transcript_id: str,
) -> tuple[str, str, float, str | None]:
    """Return assistant tail, tool blob, activity age, optional glass_status."""
    path = AGENT_TRANSCRIPTS / transcript_id / f"{transcript_id}.jsonl"
    if not path.is_file():
        return "", "", 999.0, None
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    activity_age_s = max(0.0, time.time() - mtime) if mtime else 999.0
    tail = ""
    tool_parts: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "", "", activity_age_s, None
    seen_assistant = False
    for raw in reversed(lines[-80:]):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            if not seen_assistant:
                tool_parts.append(raw)
            continue
        role = row.get("role")
        if role == "user":
            if seen_assistant:
                break
            continue
        if role != "assistant":
            continue
        seen_assistant = True
        content = (row.get("message") or {}).get("content") or []
        if not tail:
            text_chunks = [
                str(block.get("text") or "")
                for block in content
                if isinstance(block, dict)
            ]
            tail = "\n".join(chunk for chunk in text_chunks if chunk).strip()
        for block in content:
            if not isinstance(block, dict):
                continue
            if (
                block.get("type") == "tool_use"
                and block.get("name") != "GetDynamicTools"
            ):
                tool_parts.append(json.dumps(block, default=str))
    return tail, "\n".join(tool_parts), activity_age_s, None


def _turns_for_signal_scan(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in turns if not is_ear_turn(t)]


def _detect_hop_failed(turns: list[dict[str, Any]], tick: dict[str, Any]) -> bool:
    scanned = _turns_for_signal_scan(turns)
    blob = "\n".join(
        str(t.get("body") or t.get("content") or t.get("subject") or "")
        for t in scanned
    )
    if _HOP_FAILED_RE.search(blob):
        return True
    hop = tick.get("last_hop") if isinstance(tick.get("last_hop"), dict) else {}
    phase = str(hop.get("phase") or "")
    if phase == "keystroke" and hop.get("ok") is False:
        return True
    return False


def _detect_unmerged_child(turns: list[dict[str, Any]]) -> bool:
    scanned = _turns_for_signal_scan(turns)
    blob = "\n".join(
        str(t.get("body") or t.get("content") or t.get("subject") or "")
        for t in scanned
    )
    return bool(_UNMERGED_CHILD_RE.search(blob))


def load_patrol_prescreen_inputs(
    store: Any,
    *,
    root_id: str,
    transcript_id: str,
    subscription_row: dict[str, Any] | None = None,
    agent: str = "cursor",
    now: float | None = None,
    watch_dir: Path | None = None,
) -> dict[str, Any]:
    """Load live inputs for ``prescreen_house`` (transcript, turns, tick, in-flight)."""
    del agent  # reserved for surface-specific enrichment
    clock = now if now is not None else time.time()
    row = subscription_row or {}
    recent_turns = _turns_listing(store, root_id, last=10)
    tick_path = (watch_dir or WATCH_DIR) / f"liaison-{root_id}.tick.json"
    tick_state = read_state(tick_path)
    policy = effective_policy(tick_state)
    tick = {
        "now_row": str(policy.get("now_row") or ""),
        "ready": tick_state.get("ready"),
    }
    tail, tool_blob, activity_age_s, _glass = _transcript_probe(transcript_id)
    has_transcript = bool(tail)
    if not tail and recent_turns:
        for turn in reversed(recent_turns[-3:]):
            role = str(turn.get("role") or turn.get("from_agent") or "")
            if role in {"assistant", "cursor", "grok-bot", "insyncerator"}:
                tail = str(turn.get("body") or turn.get("content") or "")
                break
    if not has_transcript and not tool_blob and recent_turns:
        tool_blob = "\n".join(
            str(t.get("body") or t.get("content") or "") for t in recent_turns[-5:]
        )
    last_turn_ts = 0.0
    for turn in reversed(recent_turns):
        ts = turn.get("created_at") or turn.get("timestamp")
        if ts is not None:
            try:
                last_turn_ts = float(ts)
            except (TypeError, ValueError):
                pass
            break
    if last_turn_ts:
        activity_age_s = min(activity_age_s, max(0.0, clock - last_turn_ts))
    glass_status = str(row.get("glass_status") or _glass or "completed")
    return {
        "root_id": root_id,
        "transcript_id": transcript_id,
        "glass_status": glass_status,
        "activity_age_s": activity_age_s,
        "in_flight": _in_flight_for_root(root_id),
        "assistant_tail": tail,
        "tool_blob": tool_blob,
        "tick": tick,
        "hop_failed": _detect_hop_failed(recent_turns, tick_state),
        "unmerged_child": _detect_unmerged_child(recent_turns),
        "recent_turns": recent_turns,
    }


def patrol_mailbox_for_agent(agent: str) -> str:
    normalized = normalize_grok_to_agent(agent)
    if normalized:
        return normalized
    if str(agent).strip().lower() == "cursor":
        return "cursor"
    return "cursor"


def _assistant_tail_hash(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        role = str(msg.get("role") or msg.get("from_agent") or "")
        if role in {"assistant", "cursor", "impresario-ear"}:
            text = str(msg.get("content") or msg.get("body") or "")
            return hashlib.sha256(text.encode()).hexdigest()
    return ""


def _passive_issue_confirmation_parked(
    tail: str,
    *,
    in_flight: int,
    tool_blob: str,
    tick: dict[str, Any] | None = None,
    recent_turns: list[dict[str, Any]] | None = None,
) -> bool:
    if in_flight > 0 or not tail.strip():
        return False
    if _TOOL_MUTATION_RE.search(tool_blob):
        return False
    if _EXPLICIT_ACTION_OFFER_RE.search(tail):
        return False
    tick_row = str((tick or {}).get("now_row") or "").strip().lower()
    if tick_row not in {"", "quiet", "none"}:
        return False
    issue_in_tail = bool(_ISSUE_CONFIRM_RE.search(tail))
    if not issue_in_tail and recent_turns:
        for turn in recent_turns:
            if is_ear_turn(turn):
                continue
            sender = str(
                turn.get("from") or turn.get("from_agent") or turn.get("role") or ""
            ).lower()
            body = str(
                turn.get("body") or turn.get("content") or turn.get("subject") or ""
            )
            if sender in {"operator", "kaywan", "user"} or "operator" in sender:
                if _ISSUE_CONFIRM_RE.search(body):
                    issue_in_tail = True
                    break
            if sender and sender not in {"impresario-ear", "assistant", "cursor"}:
                break
    if not issue_in_tail:
        return False
    parked = bool(_PASSIVE_PARK_CLOSE_RE.search(tail))
    if not parked:
        parked = bool(
            re.search(
                r"(?i)\b(?:going\s*[.:—-]\s*stay|stay\s+here|idle\s+until|"
                r"wait(?:ing|s)?\s+(?:on|for)\s+the\s+next)\b",
                tail,
            )
        )
    return parked


def _promised_act_not_fired(tail: str, *, in_flight: int, tool_blob: str) -> bool:
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
    if _passive_issue_confirmation_parked(
        tail,
        in_flight=in_flight,
        tool_blob=tool_blob,
        tick=tick,
        recent_turns=recent_turns,
    ):
        signals.append("passive_issue_confirmation_parked")
    if recent_turns:
        for turn in recent_turns:
            if is_ear_turn(turn):
                return {"skip_model": True, "signals": signals, "reason": "ear_echo"}
            sender = str(
                turn.get("from") or turn.get("from_agent") or turn.get("role") or ""
            )
            if sender and sender not in {"impresario-ear"}:
                break
    assistant_hash = hashlib.sha256(tail.encode()).hexdigest() if tail else ""
    fp = _fingerprint(root_id, transcript_id, assistant_hash)
    if patrol_cooldown_active(fp):
        return {
            "skip_model": True,
            "signals": signals,
            "reason": "cooldown",
            "fingerprint": fp,
        }
    imperative = ""
    if "7139b60" in tail:
        imperative = (
            "team_dispatch review lane commit 7139b60 — do not stop at narration"
        )
    elif "passive_issue_confirmation_parked" in signals:
        imperative = _PASSIVE_ISSUE_IMPERATIVE
    elif unmerged_child:
        imperative = (
            "team_dispatch review lane commit 7139b60 — do not stop at narration"
        )
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
            loaded = load_patrol_prescreen_inputs(
                store,
                root_id=root_id,
                transcript_id=transcript_id,
                subscription_row=row,
                agent=agent,
                now=clock,
            )
            pre = prescreen_house(**loaded)
            if pre.get("skip_model") and not pre.get("signals"):
                continue
            if pre.get("reason") in {"ear_echo", "cooldown"}:
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
                    to_agent=patrol_mailbox_for_agent(agent),
                    patrol_prescreen_result=pre,
                ),
                blocking=False,
            )
            stats["enqueued"] += 1
    return stats


async def patrol_prescreen_for_evaluate(
    *,
    resolve: dict[str, Any],
    gather: dict[str, Any],
    fetch_turns: Any | None = None,
) -> dict[str, Any]:
    if str(resolve.get("mode")) != "patrol":
        return {"skip_model": False}
    jsonl = gather.get("jsonl") if isinstance(gather, dict) else {}
    status = str((jsonl or {}).get("glass_status") or "completed")
    if status == "generating":
        return {"skip_model": True, "verdict": "healthy", "reason": "generating"}
    root = str(resolve.get("root_thread") or "")
    transcript_id = str((jsonl or {}).get("transcript_id") or root)
    recent_turns = gather.get("recent_turns") if isinstance(gather, dict) else None
    if not isinstance(recent_turns, list):
        store = type("_Turns", (), {"turns": fetch_turns})()
        if fetch_turns is None:
            from bus_watch.endpoint_wake_transport import StoreClient

            store = StoreClient()
        recent_turns = _turns_listing(store, root, last=10)
    tick_path = WATCH_DIR / f"liaison-{root}.tick.json"
    tick_state = read_state(tick_path)
    policy = effective_policy(tick_state)
    tick = {
        "now_row": str(policy.get("now_row") or ""),
        "ready": tick_state.get("ready"),
    }
    tail, tool_blob, activity_age_s, _glass = _transcript_probe(transcript_id)
    has_transcript = bool(tail)
    if not tail and recent_turns:
        for turn in reversed(recent_turns[-3:]):
            role = str(turn.get("role") or turn.get("from_agent") or "")
            if role in {"assistant", "cursor", "grok-bot", "insyncerator"}:
                tail = str(turn.get("body") or turn.get("content") or "")
                break
    if not has_transcript and not tool_blob and isinstance(jsonl, dict):
        tool_blob = json.dumps(jsonl, default=str)[:8000]
    return prescreen_house(
        root_id=root,
        transcript_id=transcript_id,
        glass_status=status,
        activity_age_s=activity_age_s,
        in_flight=_in_flight_for_root(root),
        assistant_tail=tail,
        tool_blob=tool_blob,
        tick=tick,
        hop_failed=_detect_hop_failed(recent_turns, tick_state),
        unmerged_child=_detect_unmerged_child(recent_turns),
        recent_turns=recent_turns,
    )


__all__ = [
    "clear_patrol_state_for_tests",
    "load_patrol_prescreen_inputs",
    "patrol_cooldown_active",
    "patrol_mailbox_for_agent",
    "patrol_prescreen_for_evaluate",
    "prescreen_house",
    "run_patrol_pass",
    "stamp_patrol_cooldown",
]
