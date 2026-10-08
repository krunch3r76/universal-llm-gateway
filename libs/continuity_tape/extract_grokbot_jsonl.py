"""Extract ``ContinuityMessagesEnvelope`` from Grok Bot ReadTranscript output.

Input shape (confirmed 2026-10-08 in the orion operator window):

* one JSON object per line: ``{"role": ..., "message": {"content": [blocks]}}``;
* blocks: ``text`` | ``thinking`` | ``tool_use{id,name,input}`` |
  ``tool_result{tool_use_id,name,result}``;
* ReadTranscript returns pages (<=200 lines) NEWEST page first. Each page opens
  with ``Transcript of <target>, positions A–B of N:`` or
  ``Messages of <target>, positions A–B of N:``. ``<target>`` may be
  ``this conversation``, ``agent "<name>" (<uuid>)`` (en dash between
  positions; the quoted name may contain parentheses or colons), or any other
  tail-anchored phrase. When older lines remain, the page closes with
  ``Older messages remain: call ReadTranscript again ... before=A``. The oldest
  page may end with ``This is the start of the transcript.`` Lines inside one
  page run oldest-first (position A..B).

The adapter reassembles pages oldest-first into the raw (L1) messages pour
derived from the L0 pages file. It strips header/trailer/start lines, pairs
``tool_use``/``tool_result`` by id (falls back to order), drops ``thinking``
blocks, tool-result-only records and harness-injected user lines, then reuses
the Cursor walker so the envelope is byte-compatible with
``session_close transcript_messages(_path)``. Labels: ``meta.surface="grok"``,
``meta.provenance="raw"``, ``meta.sources[0].kind="grok_bot_jsonl"``.

The adapter does not write either artifact; callers do.

* L0 capture: ``cortex://notes/system/tape/captures/grok/<agent_id>/<ts>.pages.txt``
  (or the local ``/workspace/l0/<agent8>/<ts>.pages.txt``).
* L1 pour: ``cortex://notes/system/tape/pours/grok/<agent_id>.messages.json``.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
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
    r"^\s*(?:Transcript|Messages) of (?P<target>.+), positions (\d+)\s*[\u2013\u2014-]\s*(\d+) of (\d+):\s*$"
)
TRAILER_RE = re.compile(r"^\s*Older messages remain: call ReadTranscript again\b")
START_RE = re.compile(r"^\s*This is the start of the transcript\.\s*$")
# ReadTranscript middle-truncates a part to about TRUNCATION_PART_LEN chars by
# splicing a bare ``...`` (three ASCII dots, no count or brackets). A part counts
# as truncated when it contains ``...`` and its decoded length falls in
# TRUNCATION_LEN_WINDOW. This can miss a truncated part outside the window and
# can flag an untruncated ~4000-char part that contains ``...``. The bracket
# regex is kept for older/other renderers.
TRUNCATION_PART_LEN = 4000
TRUNCATION_LEN_WINDOW = (3990, 4010)
TRUNCATION_ELLIPSIS = "..."
TRUNCATION_MARKER_RE = re.compile(
    r"(?:\[\s*\.\.\.\s*\d+\s+chars?\s+truncated\s*\.\.\.\s*\]"
    r"|…\s*\[\s*truncated\s+\d+\s+chars?\s*\]\s*…"
    r"|\.\.\.\s*\[\s*truncated\s+\d+\s+chars?\s*\])",
    re.IGNORECASE,
)
_HEADER_AGENT_ID_RE = re.compile(
    r"^(?P<body>.*?)\s+\((?P<uuid>[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\)\s*$"
)
PRODUCER_ID = "grok-bot-jsonl-extractor"
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
    target: str
    header_agent_id: str | None
    lines: list[str] = field(default_factory=list)


def _header_agent_id(target: str) -> str | None:
    m = _HEADER_AGENT_ID_RE.match(target.strip())
    return m.group("uuid") if m else None


def _split_pages(text: str) -> tuple[list[_Page] | None, bool]:
    """Split concatenated ReadTranscript outputs into pages; ``None`` if no header."""
    pages: list[_Page] = []
    current: _Page | None = None
    start_sentinel = False
    for raw_line in text.splitlines():
        m = HEADER_RE.match(raw_line)
        if m:
            target = m.group("target").strip()
            current = _Page(
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                target,
                _header_agent_id(target),
            )
            pages.append(current)
            continue
        if START_RE.match(raw_line):
            start_sentinel = True
            continue
        if TRAILER_RE.match(raw_line) or not raw_line.strip():
            continue
        if current is None:
            if pages:
                continue
            return None, start_sentinel  # bare JSONL, no pages
        current.lines.append(raw_line.strip())
    if pages:
        targets = {p.target for p in pages}
        if len(targets) != 1:
            raise TranscriptPageError(f"pages disagree on target: {sorted(targets)}")
    return pages, start_sentinel


def _coverage(
    *,
    min_lo: int,
    max_hi: int,
    total: int,
    start_sentinel: bool,
) -> str:
    """Full only for a gap-free 0-based span that includes the start sentinel."""
    if min_lo == 0 and max_hi == total - 1 and start_sentinel:
        return "full"
    return "tail"


def reassemble_pages(text: str) -> tuple[list[str], dict[str, Any]]:
    """Return JSONL lines oldest-first plus coverage info.

    Accepts pages in any order (ReadTranscript hands them newest-first) and
    bare JSONL (no header) for box snapshots.
    """
    pages, start_sentinel = _split_pages(text)
    if pages is None:
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return lines, {
            "positions": None,
            "total": None,
            "coverage": "full",
            "pages": 0,
            "unrendered_positions": 0,
        }
    if not pages:
        raise TranscriptPageError("no transcript pages found")
    totals = {p.total for p in pages}
    if len(totals) != 1:
        raise TranscriptPageError(f"pages disagree on total N: {sorted(totals)}")
    total = totals.pop()
    ordered = sorted(pages, key=lambda p: (p.lo, p.hi))
    unrendered = 0
    for page in ordered:
        if page.lo > page.hi or page.hi >= total:
            raise TranscriptPageError(
                f"page {page.lo}-{page.hi} of {total}: window is not 0-based"
            )
        expected = page.hi - page.lo + 1
        got = len(page.lines)
        if got > expected:
            raise TranscriptPageError(
                f"page {page.lo}-{page.hi}: {got} lines, expected {expected}"
            )
        unrendered += expected - got
    for prev, nxt in zip(ordered, ordered[1:], strict=False):
        if prev.hi + 1 < nxt.lo:
            raise TranscriptPageError(f"gap in positions: first missing {prev.hi + 1}")
        if prev.hi >= nxt.lo:
            prev_full = len(prev.lines) == prev.hi - prev.lo + 1
            nxt_full = len(nxt.lines) == nxt.hi - nxt.lo + 1
            if not prev_full or not nxt_full:
                raise TranscriptPageError(
                    f"cannot align overlapping short page {prev.lo}-{prev.hi}"
                )
    overlaps = any(
        prev.hi >= nxt.lo for prev, nxt in zip(ordered, ordered[1:], strict=False)
    )
    if overlaps:
        by_pos: dict[int, str] = {}
        for page in ordered:
            for offset, line in enumerate(page.lines):
                pos = page.lo + offset
                prior = by_pos.get(pos)
                if prior is not None and prior != line:
                    raise TranscriptPageError(
                        f"position {pos}: overlapping pages differ"
                    )
                by_pos[pos] = line
        lo_r, hi_r = min(by_pos), max(by_pos)
        missing = [p for p in range(lo_r, hi_r + 1) if p not in by_pos]
        if missing:
            raise TranscriptPageError(f"gap in positions: first missing {missing[0]}")
        lines = [by_pos[p] for p in range(lo_r, hi_r + 1)]
    else:
        lines = [line for page in ordered for line in page.lines]
    min_lo = min(p.lo for p in ordered)
    max_hi = max(p.hi for p in ordered)
    coverage = _coverage(
        min_lo=min_lo,
        max_hi=max_hi,
        total=total,
        start_sentinel=start_sentinel,
    )
    info = {
        "positions": [min_lo, max_hi],
        "total": total,
        "coverage": coverage,
        "pages": len(pages),
        "header_target": pages[0].target,
        "header_agent_id": pages[0].header_agent_id,
        "unrendered_positions": unrendered,
        "start_sentinel": start_sentinel,
    }
    return lines, info


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


def _part_strings(block: dict[str, Any]) -> list[str]:
    """String-bearing values of one content part, before any dropping."""
    btype = block.get("type")
    found: list[str] = []
    if btype == "text" and isinstance(block.get("text"), str):
        found.append(block["text"])
    elif btype == "thinking" and isinstance(block.get("thinking"), str):
        found.append(block["thinking"])
    elif btype == "tool_result" and isinstance(block.get("result"), str):
        found.append(block["result"])
    elif btype == "tool_use":
        raw = block.get("input")
        if isinstance(raw, str):
            found.append(raw)
        elif isinstance(raw, dict):
            found.extend(v for v in raw.values() if isinstance(v, str))
    return found


def _string_truncated(text: str) -> bool:
    lo, hi = TRUNCATION_LEN_WINDOW
    if TRUNCATION_ELLIPSIS in text and lo <= len(text) <= hi:
        return True
    return TRUNCATION_MARKER_RE.search(text) is not None


def _part_candidates(block: dict[str, Any]) -> list[str]:
    found = _part_strings(block)
    if block.get("type") == "tool_use" and isinstance(block.get("input"), dict):
        found.append(json.dumps(block["input"], ensure_ascii=False))
    return found


def _truncated_part_count(records: list[dict[str, Any]]) -> int:
    n = 0
    for rec in records:
        for block in _content(rec) or []:
            if isinstance(block, dict) and any(
                _string_truncated(s) for s in _part_candidates(block)
            ):
                n += 1
    return n


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
        "memory_context_dropped": 0,
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
        if role == "user":
            user_text = _user_text(blocks)
            if user_text.lstrip().startswith("<memory_context>"):
                stats["memory_context_dropped"] += 1
                continue
            if INJECTED_USER_RE.match(user_text):
                stats["injected_user_dropped"] += 1
                continue
        kept.append({"role": role, "message": {"content": blocks}})
    return kept, stats


def _source_dict(
    *,
    agent_id: str,
    bus_identity: str | None,
    cov: dict[str, Any],
    stats: dict[str, int],
    pages_sha256: str,
    reassembled_sha256: str,
    truncated_parts: int,
    truncated_parts_kept: int,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        "kind": SOURCE_KIND,
        "agent_id": agent_id,
        "bus_identity": bus_identity,
        "positions": cov["positions"],
        "total": cov["total"],
        "pages": cov["pages"],
        "producer": {"id": PRODUCER_ID, "kind": "extractor", "version": "2"},
        "pages_sha256": pages_sha256,
        "reassembled_jsonl_sha256": reassembled_sha256,
        "truncated_parts": truncated_parts,
        "truncated_parts_kept": truncated_parts_kept,
        "unrendered_positions": cov.get("unrendered_positions", 0),
        **stats,
    }
    if cov["positions"] is not None:
        lo, hi = cov["positions"]
        source["window"] = {"lo": lo, "hi": hi, "total": cov["total"]}
        source["header_target"] = cov["header_target"]
        source["start_sentinel"] = cov["start_sentinel"]
        if cov.get("header_agent_id"):
            source["header_agent_id"] = cov["header_agent_id"]
    return source


def extract_grokbot_transcript(
    text: str,
    *,
    agent_id: str,
    bus_identity: str | None = None,
    tools: Tools = "marker",
    session_id: str | None = None,
    observed_at: str | None = None,
    capture_ref: str | None = None,
    capture_sha256: str | None = None,
) -> ContinuityMessagesEnvelope:
    """ReadTranscript pages (any order) or bare JSONL -> raw L1 messages pour."""
    raw = text.encode("utf-8")
    pages_sha256 = hashlib.sha256(raw).hexdigest()
    lines, cov = reassemble_pages(text)
    header_agent_id = cov.get("header_agent_id")
    if header_agent_id is not None and header_agent_id != agent_id:
        raise TranscriptPageError(
            f"header agent id {header_agent_id} != agent_id {agent_id}"
        )
    body = ("\n".join(lines) + "\n").encode("utf-8") if lines else b""
    parsed = _parse_records(lines)
    truncated_parts = _truncated_part_count(parsed)
    records, stats = adapt_records(parsed)
    truncated_parts_kept = _truncated_part_count(records)
    turns = _walk_turns(records, tools=tools)
    messages = _turns_to_messages(turns)
    for msg in messages:
        msg["source"] = SOURCE
    reassembled = hashlib.sha256(body).hexdigest()
    truncated = truncated_parts > 0
    cap_sha = pages_sha256 if capture_sha256 is None else capture_sha256
    meta_kwargs: dict[str, Any] = {
        "surface": "grok",
        "tools": tools,
        "tools_available": False,
        "extras": False,
        "turn_count": len(turns),
        "message_count": len(messages),
        "truncated": truncated,
        "messages_sha256": seal_messages_sha256(messages),
        "transcript_id": agent_id,
        "session_id": session_id,
        "observed_at": observed_at or datetime.now(tz=UTC).isoformat(),
        "source_sha256": reassembled,
        "coverage": cov["coverage"],
        "provenance": "raw",
        "sources": [
            _source_dict(
                agent_id=agent_id,
                bus_identity=bus_identity,
                cov=cov,
                stats=stats,
                pages_sha256=pages_sha256,
                reassembled_sha256=reassembled,
                truncated_parts=truncated_parts,
                truncated_parts_kept=truncated_parts_kept,
            )
        ],
    }
    if capture_ref is not None:
        meta_kwargs["capture"] = {
            "kind": "dom_harvest",
            "ref": capture_ref,
            "sha256": cap_sha,
            "pseudo_byte_exact": True,
        }
    meta = EnvelopeMeta(**meta_kwargs)
    envelope = ContinuityMessagesEnvelope(messages=messages, index=[], meta=meta)
    continuity_messages_extracted(
        surface="grok",
        transcript_id=agent_id,
        message_count=len(messages),
        turn_count=len(turns),
        user_turns=len(turns),
        tools=tools,
        truncated=truncated,
        source=SOURCE,
    )
    return envelope


def extract_grokbot_pages_files(
    paths: list[str | Path],
    *,
    agent_id: str,
    capture_ref: str | None = None,
    **kw: Any,
) -> ContinuityMessagesEnvelope:
    """Read L0 pages files in order and pour them through the transcript adapter."""
    blobs: list[dict[str, Any]] = []
    texts: list[str] = []
    for raw_path in paths:
        path = Path(raw_path)
        data = path.read_bytes()
        blobs.append(
            {
                "path": str(path),
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
        )
        texts.append(data.decode("utf-8"))
    joined = "\n".join(texts)
    joined_sha = hashlib.sha256(joined.encode("utf-8")).hexdigest()
    if len(blobs) == 1:
        ref: str | None = blobs[0]["path"] if capture_ref is None else capture_ref
        cap_sha = blobs[0]["sha256"]
    else:
        ref = capture_ref
        cap_sha = joined_sha
    env = extract_grokbot_transcript(
        joined,
        agent_id=agent_id,
        capture_ref=ref,
        capture_sha256=cap_sha,
        **kw,
    )
    env.meta.sources[0]["pages_files"] = blobs
    return env


def main(argv: list[str] | None = None) -> int:
    import argparse

    from continuity_tape.messages import envelope_wire_dict

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "pages_file",
        nargs="+",
        help="L0 pages files (any order) or bare JSONL; one or more paths",
    )
    ap.add_argument("--agent-id", required=True)
    ap.add_argument("--bus-identity", default=None)
    ap.add_argument("--tools", default="marker", choices=["none", "marker"])
    ap.add_argument(
        "--capture-ref",
        default=None,
        help="cortex capture ref; defaults to the single local pages-file path",
    )
    ap.add_argument("--out", default="-")
    ns = ap.parse_args(argv)
    env = extract_grokbot_pages_files(
        ns.pages_file,
        agent_id=ns.agent_id,
        bus_identity=ns.bus_identity,
        tools=ns.tools,
        capture_ref=ns.capture_ref,
    )
    payload = json.dumps(envelope_wire_dict(env), ensure_ascii=False, indent=2) + "\n"
    if ns.out == "-":
        print(payload, end="")
    else:
        Path(ns.out).write_text(payload, encoding="utf-8")
    m = env.meta
    capture = getattr(m, "capture", None)
    if hasattr(capture, "model_dump"):
        capture = capture.model_dump()
    print(
        json.dumps(
            {
                "turn_count": m.turn_count,
                "message_count": m.message_count,
                "messages_sha256": m.messages_sha256,
                "source_sha256": m.source_sha256,
                "coverage": m.coverage,
                "truncated_parts": m.sources[0]["truncated_parts"],
                "provenance": m.provenance,
                "capture": capture,
                "sources": m.sources,
            }
        ),
        file=__import__("sys").stderr,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "TRUNCATION_ELLIPSIS",
    "TRUNCATION_LEN_WINDOW",
    "TRUNCATION_MARKER_RE",
    "TRUNCATION_PART_LEN",
    "TranscriptPageError",
    "adapt_records",
    "extract_grokbot_pages_files",
    "extract_grokbot_transcript",
    "reassemble_pages",
]
