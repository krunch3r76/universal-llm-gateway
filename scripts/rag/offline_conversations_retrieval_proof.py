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


@dataclass(slots=True)
class Hit:
    rank: int
    transcript_id: str
    speaker: str
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


def _build_index(paths: list[Path], db_path: Path) -> dict[str, object]:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
        "chunk_id UNINDEXED, transcript_id UNINDEXED, speaker UNINDEXED, content)"
    )
    rows: list[tuple[str, str, str, str]] = []
    for path in paths:
        try:
            for i, chunk in enumerate(chunk_messages(path)):
                cid = f"{path.name}:{i}"
                rows.append(
                    (
                        cid,
                        str(chunk.metadata.get("transcript_id", "")),
                        str(chunk.metadata.get("speaker", "")),
                        chunk.text,
                    )
                )
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    conn.executemany(
        "INSERT INTO chunks_fts (chunk_id, transcript_id, speaker, content) VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return {"conn": conn, "row_count": len(rows)}


def _search(conn: sqlite3.Connection, query: str, *, limit: int = 5) -> list[Hit]:
    match = _tokenize_query(query)
    cur = conn.execute(
        "SELECT chunk_id, transcript_id, speaker, content, bm25(chunks_fts) AS score "
        "FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY score LIMIT ?",
        (match, limit),
    )
    hits: list[Hit] = []
    for rank, row in enumerate(cur.fetchall(), start=1):
        preview = (row[3] or "")[:160].replace("\n", " ")
        hits.append(
            Hit(
                rank=rank,
                transcript_id=str(row[1]),
                speaker=str(row[2]),
                preview=preview,
            )
        )
    return hits


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
            report["queries"][key] = {
                "query": query,
                "top5": [
                    {
                        "rank": h.rank,
                        "transcript_id": h.transcript_id,
                        "speaker": h.speaker,
                        "preview": h.preview,
                    }
                    for h in hits
                ],
            }
            ok = bool(hits) and hits[0].speaker == "user"
            ok = ok and all("[tool" not in h.preview.casefold() for h in hits)
            report["queries"][key]["pass"] = ok
            all_pass = all_pass and ok
        report["pass"] = all_pass
        print(json.dumps(report, indent=2))
        return 0 if all_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
