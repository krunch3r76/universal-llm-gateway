#!/usr/bin/env python3
"""Offline BM25 retrieval proof over sealed conversation messages (no RAG service)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_REPO), str(_REPO / "libs")]

from services.rag.chunkers.messages_chunker import chunk_messages  # noqa: E402

_QUERIES = (
    ("8", "one claude.ai seat as the master mind"),
    ("8b", "liaison skill must emphasize skill transfer on hop"),
)

_B13_ID = "cursor-2026-10-14-034000-b13"
_CDF_ID = "cursor-2026-09-22-082500-cdf"
_CDF_QUOTE_SNIPPET = "skill transfer"

_HOP_PASTE_MARKERS = (
    "briefly inform the user about the task result",
    "you are the attended liaison for house",
    "conductors on house",
    "liaison ide hop",
    "this chat monitors house",
)


@dataclass(slots=True)
class Hit:
    rank: int
    transcript_id: str
    speaker: str
    ts: str
    preview: str


def _seals_dir() -> Path:
    root = os.environ.get("CORTEX_FILES_ROOT") or str(Path.home() / "mcp-data" / "files")
    return Path(root) / "notes/system/seals"


def _in_scope_messages(path: Path) -> bool:
    name = path.name
    if not name.endswith(".messages.json"):
        return False
    if name.startswith(".lock.") or ".lock." in name:
        return False
    return True


def _tokenize_query(query: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", query.casefold())
    return " OR ".join(tokens)


def _chunk_ts(meta: dict[str, object]) -> str:
    val = meta.get("ts")
    return str(val) if val else ""


def _build_index(paths: list[Path], db_path: Path) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "chunk_id UNINDEXED, transcript_id UNINDEXED, speaker UNINDEXED, ts UNINDEXED, content)"
    )
    rows: list[tuple[str, str, str, str, str]] = []
    for path in paths:
        try:
            for i, chunk in enumerate(chunk_messages(path)):
                cid = f"{path.name}:{i}"
                meta = chunk.metadata
                rows.append(
                    (
                        cid,
                        str(meta.get("transcript_id", "")),
                        str(meta.get("speaker", "")),
                        _chunk_ts(meta),
                        chunk.text,
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    conn.executemany(
        "INSERT INTO chunks_fts (chunk_id, transcript_id, speaker, ts, content) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return {"conn": conn, "row_count": len(rows)}


def _search(conn: sqlite3.Connection, query: str, *, limit: int = 5) -> list[Hit]:
    match = _tokenize_query(query)
    cur = conn.execute(
        "SELECT chunk_id, transcript_id, speaker, ts, content, bm25(chunks_fts) AS score "
        "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
        (match, limit),
    )
    hits: list[Hit] = []
    for rank, row in enumerate(cur.fetchall(), start=1):
        preview = (row[4] or "")[:160].replace("\n", " ")
        hits.append(
            Hit(
                rank=rank,
                transcript_id=str(row[1]),
                speaker=str(row[2]),
                ts=str(row[3] or ""),
                preview=preview,
            )
        )
    return hits


def _preview_clean(preview: str) -> bool:
    lowered = preview.casefold()
    if "[tool" in lowered:
        return False
    if "<timestamp>" in lowered or "<user_query>" in lowered:
        return False
    head = lowered[:240]
    for marker in _HOP_PASTE_MARKERS:
        if head.startswith(marker) or f"\n{marker}" in head:
            return False
    if re.match(r"^resume\s+\d+\b", head) and "liaison ide hop" in head:
        return False
    return True


def _is_synthetic_preview(preview: str) -> bool:
    lowered = preview.casefold().strip()
    if not lowered:
        return True
    for marker in _HOP_PASTE_MARKERS:
        if lowered.startswith(marker):
            return True
    if lowered.startswith("[grok_bot_hidden_prompt"):
        return True
    return False


def _evaluate_query(key: str, hits: list[Hit]) -> tuple[bool, dict[str, object]]:
    top5_clean = all(_preview_clean(h.preview) for h in hits)
    details: dict[str, object] = {
        "top5_clean": top5_clean,
    }
    if key == "8":
        b13_rank = next((h.rank for h in hits if h.transcript_id == _B13_ID), None)
        details["b13_rank"] = b13_rank
        rank1_ok = bool(hits) and not _is_synthetic_preview(hits[0].preview)
        details["rank1_real_operator"] = rank1_ok
        ok = bool(hits) and top5_clean and rank1_ok
        return ok, details
    if key == "8b":
        rank1 = hits[0] if hits else None
        cdf_ok = (
            rank1 is not None
            and rank1.transcript_id == _CDF_ID
            and _CDF_QUOTE_SNIPPET in rank1.preview.casefold()
        )
        details["rank1_cdf"] = cdf_ok
        ok = bool(hits) and top5_clean and cdf_ok
        return ok, details
    ok = bool(hits) and top5_clean
    return ok, details


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit-files", type=int, default=0, help="0 = all messages files")
    args = parser.parse_args()

    seals = _seals_dir()
    paths = sorted(p for p in seals.glob("*.messages.json") if _in_scope_messages(p))
    if args.limit_files:
        paths = paths[: args.limit_files]
    if not paths:
        print(json.dumps({"error": f"no messages under {seals}"}))
        return 1

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "proof.db"
        bundle = _build_index(paths, db)
        conn: sqlite3.Connection = bundle["conn"]
        report: dict[str, object] = {
            "method": "in-process SQLite FTS5 bm25 over chunk_messages (no RAG service)",
            "files_indexed": len(paths),
            "chunks_indexed": bundle["row_count"],
            "queries": {},
        }
        all_pass = True
        for key, query in _QUERIES:
            hits = _search(conn, query)
            ok, eval_details = _evaluate_query(key, hits)
            report["queries"][key] = {
                "query": query,
                "top5": [
                    {
                        "rank": h.rank,
                        "transcript_id": h.transcript_id,
                        "speaker": h.speaker,
                        "ts": h.ts,
                        "preview": h.preview,
                    }
                    for h in hits
                ],
                **eval_details,
                "pass": ok,
            }
            all_pass = all_pass and ok
        report["pass"] = all_pass
        print(json.dumps(report, indent=2))
        return 0 if all_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
