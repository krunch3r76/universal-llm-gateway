#!/usr/bin/env python3
"""Backfill transcript:{session_id} L1 provenance (source_jsonl_uri + sha256)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "libs"))

from cortex_store.dispatch_ops import execute_op  # noqa: E402

_SEALS_REL = Path("notes/system/seals")
_AGENT = "cursor-sdk"
_SESSION = "backfill-transcript-l1-links"


def _files_root() -> Path:
    env = os.environ.get("CORTEX_FILES_ROOT")
    if env:
        return Path(env)
    return Path.home() / "mcp-data" / "files"


def _session_id_from_source(path: Path) -> str:
    name = path.name
    if not name.endswith(".source.jsonl"):
        raise ValueError(name)
    return name[: -len(".source.jsonl")]


def _surface_from_session(session_id: str) -> str:
    if session_id.startswith("cursor-"):
        return "cursor"
    if session_id.startswith("grok") or "grok" in session_id:
        return "grok-bot"
    if session_id.startswith("claude") or "anthropic" in session_id:
        return "web-anthropic"
    return "other"


def _is_excluded_name(name: str) -> bool:
    return name.startswith(".lock.") or ".lock." in name


def _collect(seals_dir: Path) -> dict[str, object]:
    messages = [p for p in seals_dir.glob("*.messages.json") if not _is_excluded_name(p.name)]
    sources = [p for p in seals_dir.glob("*.source.jsonl") if not _is_excluded_name(p.name)]
    msg_ids = {p.name[: -len(".messages.json")] for p in messages}
    src_ids = {_session_id_from_source(p) for p in sources}
    cursor_messages = sum(1 for p in messages if p.name.startswith("cursor-"))
    cursor_sources = sum(1 for p in sources if p.name.startswith("cursor-"))
    surface_counts: Counter[str] = Counter()
    for sid in msg_ids:
        surface_counts[_surface_from_session(sid)] += 1
    for sid in src_ids:
        surface_counts[f"{_surface_from_session(sid)}_l1"] += 1
    return {
        "messages_json": len(messages),
        "cursor_messages_json": cursor_messages,
        "source_jsonl": len(sources),
        "cursor_source_jsonl": cursor_sources,
        "pairs": len(msg_ids & src_ids),
        "l2_without_l1": sorted(msg_ids - src_ids),
        "l1_without_l2": sorted(src_ids - msg_ids),
        "surface_counts": dict(surface_counts),
        "sources": sources,
    }


def _apply_one(source_path: Path, *, dry_run: bool) -> dict[str, object]:
    session_id = _session_id_from_source(source_path)
    entity_id = f"transcript:{session_id}"
    raw = source_path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    rel = source_path.relative_to(_files_root())
    uri = f"files://{rel.as_posix()}"
    patch: dict[str, Any] = {
        "attributes": {
            "source_jsonl_uri": uri,
            "source_jsonl_sha256": sha,
        }
    }
    if dry_run:
        return {"entity_id": entity_id, "dry_run": True, **patch}
    result = execute_op(
        "entity_update",
        {
            "entity_id": entity_id,
            "agent": _AGENT,
            "session_id": _SESSION,
            **patch,
        },
    )
    return {"entity_id": entity_id, "result": result}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report counts and planned updates only",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Run entity_update for each .source.jsonl (requires live cortex DB)",
    )
    args = parser.parse_args()
    if args.apply and args.dry_run:
        print("Use either --dry-run or --apply, not both", file=sys.stderr)
        return 2
    if not args.apply:
        args.dry_run = True

    seals_dir = _files_root() / _SEALS_REL
    if not seals_dir.is_dir():
        print(json.dumps({"error": f"seals dir missing: {seals_dir}"}, indent=2))
        return 1

    inventory = _collect(seals_dir)
    report: dict[str, object] = {
        "seals_dir": str(seals_dir),
        "messages_json_total": inventory["messages_json"],
        "cursor_messages_json": inventory["cursor_messages_json"],
        "source_jsonl_total": inventory["source_jsonl"],
        "cursor_source_jsonl": inventory["cursor_source_jsonl"],
        "pairs": inventory["pairs"],
        "l2_without_l1_count": len(inventory["l2_without_l1"]),
        "l1_without_l2_count": len(inventory["l1_without_l2"]),
        "surface_counts": inventory["surface_counts"],
        "entity_updates_planned": len(inventory["sources"]),
        "entity_update_op": "execute_op(entity_update, {entity_id, attributes: {source_jsonl_uri, source_jsonl_sha256}})",
        "mode": "dry_run" if args.dry_run else "apply",
    }
    updates: list[dict[str, object]] = []
    if args.apply:
        for source_path in inventory["sources"]:
            updates.append(_apply_one(source_path, dry_run=False))
        report["updates"] = updates
        report["applied"] = sum(
            1
            for u in updates
            if isinstance(u.get("result"), dict) and "error" not in u["result"]
        )
    else:
        for source_path in inventory["sources"][:5]:
            updates.append(_apply_one(source_path, dry_run=True))
        report["sample_updates"] = updates
        report["apply_command"] = (
            f"CORTEX_FILES_ROOT={_files_root()} "
            f"{sys.executable} scripts/rag/backfill_transcript_l1_links.py --apply"
        )

    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
