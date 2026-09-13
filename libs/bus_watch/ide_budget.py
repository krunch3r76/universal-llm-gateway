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

import json
import re
from pathlib import Path
from typing import Any

AGENT_TRANSCRIPTS = (
    Path.home()
    / ".cursor/projects/mnt-torus-projects-universal-llm-gateway/agent-transcripts"
)
IDE_BUDGET_SOURCE = "ide.transcript"
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_TIP_CP_RE = re.compile(r"tip_cp=(\d+)")


def ide_holder_transcript(lock: dict[str, Any]) -> str | None:
    """Transcript id from an ``ide:<transcript_id>`` seat holder; ``None`` for
    ``sdk:`` holders and for the legacy ``ide:<root>`` co-hold form."""
    holder = str(lock.get("holder") or "")
    if not holder.startswith("ide:"):
        return None
    candidate = holder.split(":", 1)[1]
    return candidate if _UUID_RE.match(candidate) else None


def newest_resume_transcript(
    root_id: str, transcripts_dir: Path = AGENT_TRANSCRIPTS
) -> str | None:
    """Transcript id of the tab most recently writing under ``resume <root>``.

    Budget wants the tab that is *consuming* context now, so mtime-newest wins
    (the hop helper prefers the highest ``tip_cp=`` for a different question —
    which tab to checkpoint against). Falls back to this when the seat lock has
    no ``ide:<transcript_id>`` holder, e.g. an attended tab that never claimed.
    """
    if not transcripts_dir.is_dir():
        return None
    needle = f"resume {root_id}"
    best: tuple[float, str] | None = None
    for path in transcripts_dir.glob("*/*.jsonl"):
        try:
            with path.open(encoding="utf-8") as fh:
                first_line = fh.readline()
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if needle in first_line and (best is None or mtime > best[0]):
            best = (mtime, path.parent.name)
    return best[1] if best else None


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


def estimate_tokens(measure: dict[str, Any], *, tokens_per_tool_call: int) -> int:
    """Prose bytes / 4 plus an allowance per tool call for the unstored results."""
    return int(measure.get("bytes") or 0) // 4 + int(
        measure.get("tool_calls") or 0
    ) * int(tokens_per_tool_call)


def measure_ide_tab(
    root_id: str,
    lock: dict[str, Any],
    policy: dict[str, Any],
    *,
    transcripts_dir: Path = AGENT_TRANSCRIPTS,
) -> dict[str, Any] | None:
    """Resolve the live IDE tab for ``root_id`` and estimate its window use.

    Returns ``None`` when no tab transcript can be found (headless-only house).
    ``window_limit_tokens`` is ``policy.ide_window_tokens`` — the operator picks the
    tab model in the picker, so the harness cannot read it; the default is the
    256k class the house has been running on (Grok 4.6).
    """
    transcript_id = ide_holder_transcript(lock) or newest_resume_transcript(
        root_id, transcripts_dir
    )
    if not transcript_id:
        return None
    path = transcripts_dir / transcript_id / f"{transcript_id}.jsonl"
    if not path.is_file():
        return None
    measure = measure_transcript(path)
    per_call = int(policy.get("ide_tokens_per_tool_call") or 1500)
    return {
        "transcript_id": transcript_id,
        "holder_basis": "seat_lock" if ide_holder_transcript(lock) else "resume_mtime",
        "used_tokens": estimate_tokens(measure, tokens_per_tool_call=per_call),
        "window_limit_tokens": int(policy.get("ide_window_tokens") or 256_000),
        "tokens_per_tool_call": per_call,
        **measure,
    }


__all__ = [
    "AGENT_TRANSCRIPTS",
    "IDE_BUDGET_SOURCE",
    "estimate_tokens",
    "ide_holder_transcript",
    "measure_ide_tab",
    "measure_transcript",
    "newest_resume_transcript",
]
