"""Extract ``ContinuityMessagesEnvelope`` from Grok Bot ReadTranscript output.

Input shape (confirmed 2026-10-08 in the orion operator window):

* one JSON object per line: ``{"role": ..., "message": {"content": [blocks]}}``;
* blocks: ``text`` | ``thinking`` | ``tool_use{id,name,input}`` |
  ``tool_result{tool_use_id,name,result}``;
* ReadTranscript returns pages (<=200 lines) NEWEST page first. Each page opens
  with ``Transcript of this conversation, positions A–B of N:`` and, when older
  lines remain, closes with ``Older messages remain: call ReadTranscript again
  ... before=A``. Lines inside one page run oldest-first (position A..B).

The adapter reassembles pages oldest-first, strips header/trailer lines, pairs
``tool_use``/``tool_result`` by id (falls back to order), drops ``thinking``
blocks, tool-result-only records and harness-injected user lines, then reuses
the Cursor walker so the envelope is byte-compatible with
``session_close transcript_messages(_path)``. Labels: ``meta.surface="grok"``,
``meta.sources[0].kind="grok_bot_jsonl"`` (no schema change).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from continuity_tape.events import continuity_messages_extracted
from continuity_tape.extract_jsonl import _turns_to_messages, _walk_turns
from continuity_tape.messages import (
    ContinuityMessagesEnvelope,
    EnvelopeMeta,
    Tools,
    seal_messages_sha256,
)

SOURCE = "grok-bot-jsonl"
SOURCE_KIND = "grok_bot_jsonl"

HEADER_RE = re.compile(
    r"^\s*Transcript of this conversation, positions (\d+)\s*[\u2013\u2014-]\s*(\d+) of (\d+):\s*$"
)
TRAILER_RE = re.compile(r"^\s*Older messages remain: call ReadTranscript again\b")
INJECTED_USER_RE = re.compile(
    r"^\s*(?:\[GROK_BOT_HIDDEN_PROMPT|\[SAND_HIDDEN_PROMPT|\[A background task\b"
    r"|\[event\b|<agent_profile_update>|<system_reminder>|<instructions_update>)"
)


class TranscriptPageError(ValueError):
    """Pages are malformed, overlap inconsistently, or leave a gap."""


@dataclass
class _Page:
    lo: int
    hi: int
    total: int
    lines: list[str] = field(default_factory=list)


def _split_pages(text: str) -> list[_Page] | None:
    """Split concatenated ReadTranscript outputs into pages; ``None`` if no header."""
    pages: list[_Page] = []
    current: _Page | None = None
    for raw_line in text.splitlines():
        m = HEADER_RE.match(raw_line)
        if m:
            current = _Page(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            pages.append(current)
            continue
        if TRAILER_RE.match(raw_line) or not raw_line.strip():
            continue
        if current is None:
            if pages:
                continue
            return None  # bare JSONL, no pages
        current.lines.append(raw_line.strip())
    return pages


def reassemble_pages(text: str) -> tuple[list[str], dict[str, Any]]:
    """Return JSONL lines oldest-first plus coverage info.

    Accepts pages in any order (ReadTranscript hands them newest-first) and
    bare JSONL (no header) for box snapshots.
    """
    pages = _split_pages(text)
    if pages is None:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines, {"positions": None, "total": None, "coverage": "full", "pages": 0}
    if not pages:
        raise TranscriptPageError("no transcript pages found")
    totals = {p.total for p in pages}
    if len(totals) != 1:
        raise TranscriptPageError(f"pages disagree on total N: {sorted(totals)}")
    total = totals.pop()
    by_pos: dict[int, str] = {}
    for page in sorted(pages, key=lambda p: p.lo):
        expected = page.hi - page.lo + 1
        if len(page.lines) != expected:
            raise TranscriptPageError(
                f"page {page.lo}-{page.hi}: {len(page.lines)} lines, expected {expected}"
            )
        for offset, line in enumerate(page.lines):
            pos = page.lo + offset
            prior = by_pos.get(pos)
            if prior is not None and prior != line:
                raise TranscriptPageError(f"position {pos}: overlapping pages differ")
            by_pos[pos] = line
    lo, hi = min(by_pos), max(by_pos)
    missing = [p for p in range(lo, hi + 1) if p not in by_pos]
    if missing:
        raise TranscriptPageError(f"gap in positions: first missing {missing[0]}")
    coverage = "full" if lo == 1 and hi == total else "tail"
    info = {
        "positions": [lo, hi],
        "total": total,
        "coverage": coverage,
        "pages": len(pages),
    }
    return [by_pos[p] for p in range(lo, hi + 1)], info


def _parse_records(lines: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for n, line in enumerate(lines, start=1):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"grok transcript line {n}: invalid JSON ({exc})") from exc
        if isinstance(rec, dict):
            records.append(rec)
    return records


def _content(rec: dict[str, Any]) -> list[Any] | None:
    content = (rec.get("message") or {}).get("content")
    return content if isinstance(content, list) else None


def _user_text(content: list[Any]) -> str:
    return "\n\n".join(
        b.get("text", "")
        for b in content
        if isinstance(b, dict)
        and b.get("type") == "text"
        and isinstance(b.get("text"), str)
    ).strip()


def adapt_records(
    records: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Hygiene pass: pair tools, drop thinking / tool results / injected user lines."""
    stats = {
        "records_in": len(records),
        "thinking_blocks_dropped": 0,
        "tool_result_records_dropped": 0,
        "injected_user_dropped": 0,
        "tool_pairs_by_id": 0,
        "tool_pairs_by_order": 0,
        "tool_use_unpaired": 0,
    }
    # Pair tool_use <-> tool_result: by id first, then FIFO order for id-less blocks.
    uses: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for rec in records:
        for b in _content(rec) or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                uses.append(b)
            elif isinstance(b, dict) and b.get("type") == "tool_result":
                results.append(b)
    by_id = {b["tool_use_id"]: b for b in results if b.get("tool_use_id")}
    leftover_results = [b for b in results if not b.get("tool_use_id")]
    unmatched_uses: list[dict[str, Any]] = []
    for use in uses:
        res = by_id.pop(use["id"], None) if use.get("id") else None
        if res is not None:
            stats["tool_pairs_by_id"] += 1
            if not use.get("name") and res.get("name"):
                use["name"] = res["name"]
        else:
            unmatched_uses.append(use)
    leftover_results.extend(by_id.values())
    for use in unmatched_uses:
        if leftover_results:
            res = leftover_results.pop(0)
            stats["tool_pairs_by_order"] += 1
            if not use.get("name") and res.get("name"):
                use["name"] = res["name"]
        else:
            stats["tool_use_unpaired"] += 1

    kept: list[dict[str, Any]] = []
    for rec in records:
        role = rec.get("role")
        content = _content(rec)
        if content is None:
            continue
        blocks = []
        for b in content:
            btype = b.get("type") if isinstance(b, dict) else None
            if btype == "thinking":
                stats["thinking_blocks_dropped"] += 1
                continue
            if btype == "tool_result":
                continue
            blocks.append(b)
        if not blocks:
            if any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in content
            ):
                stats["tool_result_records_dropped"] += 1
            continue
        if role == "tool":
            stats["tool_result_records_dropped"] += 1
            continue
        if role == "user" and INJECTED_USER_RE.match(_user_text(blocks)):
            stats["injected_user_dropped"] += 1
            continue
        kept.append({"role": role, "message": {"content": blocks}})
    return kept, stats


def extract_grokbot_transcript(
    text: str,
    *,
    agent_id: str,
    bus_identity: str | None = None,
    tools: Tools = "marker",
    session_id: str | None = None,
    observed_at: str | None = None,
) -> ContinuityMessagesEnvelope:
    """ReadTranscript pages (any order) or bare JSONL -> continuity envelope."""
    lines, cov = reassemble_pages(text)
    body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    records, stats = adapt_records(_parse_records(lines))
    turns = _walk_turns(records, tools=tools)
    messages = _turns_to_messages(turns)
    for msg in messages:
        msg["source"] = SOURCE
    meta = EnvelopeMeta(
        surface="grok",
        tools=tools,
        tools_available=False,
        extras=False,
        turn_count=len(turns),
        message_count=len(messages),
        truncated=False,
        messages_sha256=seal_messages_sha256(messages),
        transcript_id=agent_id,
        session_id=session_id,
        observed_at=observed_at or datetime.now(tz=UTC).isoformat(),
        source_sha256=hashlib.sha256(body).hexdigest(),
        coverage=cov["coverage"],
        sources=[
            {
                "kind": SOURCE_KIND,
                "agent_id": agent_id,
                "bus_identity": bus_identity,
                "positions": cov["positions"],
                "total": cov["total"],
                "pages": cov["pages"],
                **stats,
            }
        ],
    )
    envelope = ContinuityMessagesEnvelope(messages=messages, index=[], meta=meta)
    continuity_messages_extracted(
        surface="grok",
        transcript_id=agent_id,
        message_count=len(messages),
        turn_count=len(turns),
        user_turns=len(turns),
        tools=tools,
        truncated=False,
        source=SOURCE,
    )
    return envelope


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path

    from continuity_tape.messages import envelope_wire_dict

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "pages_file",
        help="concatenated ReadTranscript outputs (any order) or bare JSONL",
    )
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--bus-identity", default=None)
    ap.add_argument("--tools", default="marker", choices=["none", "marker"])
    ap.add_argument("--out", default="-")
    ns = ap.parse_args(argv)
    env = extract_grokbot_transcript(
        Path(ns.pages_file).read_text(encoding="utf-8"),
        agent_id=ns.agent_id,
        bus_identity=ns.bus_identity,
        tools=ns.tools,
    )
    payload = json.dumps(envelope_wire_dict(env), ensure_ascii=False, indent=2) + "\n"
    if ns.out == "-":
        print(payload, end="")
    else:
        Path(ns.out).write_text(payload, encoding="utf-8")
    m = env.meta
    print(
        json.dumps(
            {
                "turn_count": m.turn_count,
                "message_count": m.message_count,
                "messages_sha256": m.messages_sha256,
                "source_sha256": m.source_sha256,
                "coverage": m.coverage,
                "sources": m.sources,
            }
        ),
        file=__import__("sys").stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "TranscriptPageError",
    "adapt_records",
    "extract_grokbot_transcript",
    "reassemble_pages",
]
