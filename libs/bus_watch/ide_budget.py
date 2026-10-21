"""IDE-tab context budget — estimate a Cursor tab's window use from its transcript.

An attended liaison tab has no usage stream: GIW ``dispatch-usage-live`` covers
only headless ``sdk:`` holders, so the digest could never raise
``CONTEXT_BUDGET`` for an IDE seat. The 10534 tab (2026-09-12, Grok 4.6, 256k)
ran 852 tool calls over eleven hours, compacted repeatedly, and lost its
commission with nothing telling the seat or the operator to checkpoint.

The agent-transcripts JSONL is the one observable the hub has for a tab: user
and assistant turns plus ``tool_use`` inputs — tool *results* are not stored.
Tokens are therefore an estimate that carries its basis (status-basis
invariant): prose bytes / 4 plus a per-tool-call allowance for the unstored
results. The seat's own usage reading wins when it has one.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from cursor_bridge.cdp_pages import list_pages
from cursor_bridge.cursor_agents_hop import (
    cursor_debug_url_for_gui_host,
    pick_chat_page,
)
from cursor_bridge.glass_services_js import probe_glass_composer_budget_js

from bus_watch.fable_lock import WATCH_DIR
from bus_watch.ide_budget_exclude import (
    AGENT_TRANSCRIPTS,
    BUDGET_EXCLUDE_QUIESCENT_S,
    active_budget_excludes,
    budget_exclude_path,
    pin_retired_resume_transcript,
)

IDE_BUDGET_SOURCE = "ide.transcript"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TIP_CP_RE = re.compile(r"tip_cp=(\d+)(?!\d)")
_TIP_CP_NEEDLE_RE = re.compile(r"^tip_cp=(\d+)$")


def ide_holder_transcript(lock: dict[str, Any]) -> str | None:
    """Transcript id from an ``ide:<transcript_id>`` seat holder; ``None`` for
    ``sdk:`` holders and for the legacy ``ide:<root>`` co-hold form."""
    holder = str(lock.get("holder") or "")
    if not holder.startswith("ide:"):
        return None
    candidate = holder.split(":", 1)[1]
    return candidate if _UUID_RE.match(candidate) else None


def first_line_matches(
    needle: str, transcripts_dir: Path = AGENT_TRANSCRIPTS
) -> list[tuple[int, float, str]]:
    """``(tip_cp, mtime, transcript_id)`` for every tab whose first user line
    contains ``needle``; ``tip_cp`` is ``-1`` when the opener carries none.

    Bare ``tip_cp=N`` needles require the decoded body to start with
    ``resume <root>`` and match ``tip_cp=N(?!\\d)`` — raw substring alone hit
    stale foreign roots (a:38474 Sep-12 ``06712639`` / tip_cp=75). Other
    needles keep substring match on the first JSONL line.
    """
    if not transcripts_dir.is_dir():
        return []
    tip_only = _TIP_CP_NEEDLE_RE.match((needle or "").strip())
    if tip_only is not None:
        # Bare tip_cp=N is root-ambiguous — raw substring used to hit foreign
        # hops (a:38474 Sep-12 ``06712639`` / tip_cp=75 on resume 10534). Land
        # proof needs ``first_line_matches_land`` with an explicit root_id.
        return []
    rows: list[tuple[int, float, str]] = []
    for path in transcripts_dir.glob("*/*.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if needle in first_line:
            # Digit-boundary tip extract so tip_cp=7 ≠ tip_cp=75 in the JSONL line.
            m = _TIP_CP_RE.search(first_line)
            rows.append((int(m.group(1)) if m else -1, mtime, path.parent.name))
    return rows


def newest_resume_transcript(
    root_id: str,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> str | None:
    """Transcript id of the tab most recently writing under ``resume <root>``.

    Budget wants the tab that is *consuming* context now, so mtime-newest wins
    (the hop helper prefers the highest ``tip_cp=`` for a different question —
    which tab to checkpoint against). Falls back to this when the seat lock has
    no ``ide:<transcript_id>`` holder, e.g. an attended tab that never claimed.
    ``exclude`` drops hop-retired ids (a:38328) still writing under the same opener.
    """
    blocked = exclude or set()
    rows = [
        row
        for row in first_line_matches(f"resume {root_id}", transcripts_dir)
        if row[2] not in blocked
    ]
    return max(rows, key=lambda row: row[1])[2] if rows else None


def resume_match_count(
    root_id: str,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
    *,
    exclude: set[str] | frozenset[str] | None = None,
) -> int:
    """How many ``resume <root>`` tabs remain after hop-retire excludes."""
    blocked = exclude or set()
    return sum(
        1
        for row in first_line_matches(f"resume {root_id}", transcripts_dir)
        if row[2] not in blocked
    )


def measure_transcript(path: Path) -> dict[str, Any]:
    """Count what the JSONL records: bytes, turns, tool calls, hop ``tip_cp``."""
    out: dict[str, Any] = {
        "bytes": 0,
        "user_turns": 0,
        "assistant_msgs": 0,
        "tool_calls": 0,
        "tip_cp": None,
    }
    with path.open("rb") as fh:
        for raw in fh:
            out["bytes"] += len(raw)
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                continue
            role = row.get("role")
            content = (row.get("message") or {}).get("content") or []
            if role == "user":
                out["user_turns"] += 1
                if out["tip_cp"] is None and out["user_turns"] == 1:
                    for block in content:
                        m = _TIP_CP_RE.search(str(block.get("text") or ""))
                        if m:
                            out["tip_cp"] = int(m.group(1))
            elif role == "assistant":
                out["assistant_msgs"] += 1
                out["tool_calls"] += sum(
                    1 for block in content if block.get("type") == "tool_use"
                )
    return out


async def _probe_glass_composer_budget_async(
    transcript_id: str,
    gui_host: str,
    *,
    timeout_s: float = 2.5,
) -> dict[str, Any] | None:
    import websockets

    origin = cursor_debug_url_for_gui_host(gui_host)
    try:
        pages = await asyncio.to_thread(list_pages, origin, timeout_s=min(timeout_s, 2.0))
    except OSError:
        return None
    page = pick_chat_page(pages)
    ws_url = (page or {}).get("web_socket_debugger_url") or ""
    if not ws_url:
        return None
    expression = probe_glass_composer_budget_js(transcript_id)
    try:
        async with websockets.connect(ws_url, max_size=2_000_000) as ws:
            await ws.send(
                json.dumps(
                    {
                        "id": 1,
                        "method": "Runtime.evaluate",
                        "params": {
                            "expression": expression,
                            "awaitPromise": True,
                            "returnByValue": True,
                        },
                    }
                )
            )
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout_s)
    except (TimeoutError, OSError, json.JSONDecodeError):
        return None
    payload = json.loads(raw)
    value = ((payload.get("result") or {}).get("result") or {}).get("value")
    if not isinstance(value, dict) or not value.get("ok"):
        return None
    used = int(value.get("contextTokensUsed") or 0)
    limit = int(value.get("contextTokenLimit") or 0)
    if used <= 0 or limit <= 0:
        return None
    return value


def probe_glass_composer_budget(
    transcript_id: str,
    gui_host: str,
    *,
    timeout_s: float = 2.5,
) -> dict[str, Any] | None:
    """Live Glass composer budget via CDP; ``None`` when unreachable (tests, mcp-server)."""
    try:
        return asyncio.run(
            _probe_glass_composer_budget_async(
                transcript_id, gui_host, timeout_s=timeout_s
            )
        )
    except RuntimeError:
        return None


def estimate_tokens(measure: dict[str, Any], *, tokens_per_tool_call: int) -> int:
    """Prose bytes / 4 plus an allowance per tool call for the unstored results."""
    return int(measure.get("bytes") or 0) // 4 + int(
        measure.get("tool_calls") or 0
    ) * int(tokens_per_tool_call)


def apply_glass_probe_to_budget(
    budget: dict[str, Any],
    glass: dict[str, Any],
    *,
    used_tokens_fallback: int,
    window_limit_fallback: int,
) -> None:
    """Merge live Glass CDP fields onto a normalized IDE budget dict in place."""
    budget["used_tokens"] = int(glass.get("contextTokensUsed") or used_tokens_fallback)
    budget["window_limit_tokens"] = int(
        glass.get("contextTokenLimit") or window_limit_fallback
    )
    budget["glass_context_percent"] = glass.get("contextUsagePercent")
    budget["glass_status"] = glass.get("status")
    budget["glass_model"] = glass.get("model")
    budget["glass_summarized_tokens"] = int(glass.get("glass_summarized_tokens") or 0)
    budget["glass_headers_count"] = int(glass.get("glass_headers_count") or 0)
    budget["budget_probe"] = "glass.cdp"


def measure_ide_tab(
    root_id: str,
    lock: dict[str, Any],
    policy: dict[str, Any],
    *,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
    watch_dir: Path = WATCH_DIR,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Resolve the live IDE tab for ``root_id`` and estimate its window use.

    Returns ``None`` when no tab transcript can be found (headless-only house).
    When the JSONL is missing but Glass CDP succeeds, returns a normalized budget
    dict (transcript metadata, ``tool_calls=0``, ``bytes=0``, Glass token fields,
    ``budget_probe=glass.cdp``) — not the raw probe payload.

    ``window_limit_tokens`` is ``policy.ide_window_tokens`` when Glass is absent —
    the operator picks the tab model in the picker, so the harness cannot read it;
    the default is the 256k class the house has been running on (Grok 4.7).

    Seat-lock ``ide:<uuid>`` is the budget identity. Resume-mtime is display-only
    fallback and must not drive CONTEXT_BUDGET when multiple resume tabs exist
    (a:38328).
    """
    locked = ide_holder_transcript(lock)
    excluded = active_budget_excludes(
        root_id,
        transcripts_dir=transcripts_dir,
        watch_dir=watch_dir,
        now=now,
    )
    matches = resume_match_count(
        root_id, transcripts_dir, exclude=excluded
    )
    transcript_id = locked or newest_resume_transcript(
        root_id, transcripts_dir, exclude=excluded
    )
    if not transcript_id:
        return None
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    gui_host = str(policy.get("gui_host") or ":8")
    per_call = int(policy.get("ide_tokens_per_tool_call") or 1500)
    window_limit = int(policy.get("ide_window_tokens") or 256_000)
    if not path.is_file():
        glass = probe_glass_composer_budget(transcript_id, gui_host)
        if glass:
            out: dict[str, Any] = {
                "transcript_id": transcript_id,
                "holder_basis": "seat_lock" if locked else "resume_mtime",
                "resume_match_count": matches,
                "excluded_resume_ids": sorted(excluded),
                "tokens_per_tool_call": per_call,
                "tool_calls": 0,
                "bytes": 0,
                "used_tokens": 0,
                "window_limit_tokens": window_limit,
            }
            apply_glass_probe_to_budget(
                out,
                glass,
                used_tokens_fallback=0,
                window_limit_fallback=window_limit,
            )
            return out
        return None
    measure = measure_transcript(path)
    used_tokens = estimate_tokens(measure, tokens_per_tool_call=per_call)
    out = {
        "transcript_id": transcript_id,
        "holder_basis": "seat_lock" if locked else "resume_mtime",
        "resume_match_count": matches,
        "excluded_resume_ids": sorted(excluded),
        "used_tokens": used_tokens,
        "window_limit_tokens": window_limit,
        "tokens_per_tool_call": per_call,
        **measure,
    }
    glass = probe_glass_composer_budget(transcript_id, gui_host)
    if glass:
        apply_glass_probe_to_budget(
            out,
            glass,
            used_tokens_fallback=used_tokens,
            window_limit_fallback=window_limit,
        )
    return out


def ide_transcript_probe_resolved(
    lock: dict[str, Any],
    *,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
) -> bool:
    """Whether the seat lock's ``ide:<transcript_id>`` maps to a live JSONL probe.

    Returns ``False`` when the holder is not a measurable tab, the expected file
    is missing, or the file looks stale relative to lock activity (10479 a:33450:
    ticker read a dead JSONL — ``tool_calls=1`` while the tab had passed 120).
    Unresolved probes must not drive idle forfeit."""
    transcript_id = ide_holder_transcript(lock)
    if not transcript_id:
        return False
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    if not path.is_file():
        return False
    turns_seen = lock.get("turns_seen")
    if turns_seen is None:
        return True
    if int(turns_seen) <= 0:
        return True
    measure = measure_transcript(path)
    tool_calls = int(measure.get("tool_calls") or 0)
    user_turns = int(measure.get("user_turns") or 0)
    if tool_calls <= 1 and user_turns <= 2:
        return False
    return True


def ide_holder_idle_s(
    lock: dict[str, Any],
    *,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
    now: float | None = None,
) -> float | None:
    """Seconds since the ``ide:<transcript_id>`` holder's tab last wrote its
    transcript — the only liveness the hub can read for an attended seat.
    ``None`` when the holder is not a measurable tab."""
    if not ide_transcript_probe_resolved(lock, transcripts_dir=transcripts_dir):
        return None
    transcript_id = ide_holder_transcript(lock)
    assert transcript_id is not None
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return max(0.0, (now if now is not None else time.time()) - mtime)


__all__ = [
    "AGENT_TRANSCRIPTS",
    "BUDGET_EXCLUDE_QUIESCENT_S",
    "IDE_BUDGET_SOURCE",
    "active_budget_excludes",
    "budget_exclude_path",
    "estimate_tokens",
    "first_line_matches",
    "ide_holder_idle_s",
    "ide_holder_transcript",
    "ide_transcript_probe_resolved",
    "measure_ide_tab",
    "measure_transcript",
    "probe_glass_composer_budget",
    "newest_resume_transcript",
    "pin_retired_resume_transcript",
    "resume_match_count",
]
