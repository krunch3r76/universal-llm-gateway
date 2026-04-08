#!/usr/bin/env python3
"""Incrementally index a static corpus without watcher registration.

Use this for large, mostly immutable corpora (for example /mnt/torus/corpus/occult)
where indexing should progress across restarts but stay interruptible for normal
inference traffic.

Behavior:
- Discovers immediate child directories under ``--corpus-root``.
- Indexes a bounded number of batches per run (default: 1 directory per run).
- Persists completed batch paths in a JSON state file so reruns continue from the
  next unfinished directory.
- Calls RAG ``POST /index_directory`` (no watcher changes, no scope changes).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WORKSPACE))
sys.path.insert(0, str(WORKSPACE / "libs"))

from transport_utils import make_sync_client, resolve_rag_base_url  # noqa: E402

DEFAULT_CORPUS_ROOT = Path("/mnt/torus/corpus/occult")
DEFAULT_STATE_FILE = Path.home() / ".rag" / "state" / "static-corpus-index-state.json"
DEFAULT_EXTENSIONS = [".pdf", ".epub", ".txt", ".md", ".html", ".htm"]


@dataclass(slots=True)
class IndexState:
    corpus_root: str
    completed: set[str]
    updated_at: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Incrementally index a static corpus via RAG /index_directory."
    )
    parser.add_argument(
        "--corpus-root",
        type=Path,
        default=DEFAULT_CORPUS_ROOT,
        help=f"Static corpus root (default: {DEFAULT_CORPUS_ROOT})",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=DEFAULT_STATE_FILE,
        help=f"Progress state JSON path (default: {DEFAULT_STATE_FILE})",
    )
    parser.add_argument(
        "--rag-url",
        default=resolve_rag_base_url(),
        help="RAG base URL (default: resolved from stargate/rag config)",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=1,
        help="Maximum directories to index this run (default: 1)",
    )
    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=2.0,
        help="Pause between batches in this run (default: 2.0)",
    )
    parser.add_argument(
        "--extensions",
        default=",".join(DEFAULT_EXTENSIONS),
        help="Comma-separated extensions for /index_directory",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Pass force=true to /index_directory",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Reset progress state before processing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print pending batches without indexing",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_extensions(raw: str) -> list[str]:
    parsed = [part.strip().lower() for part in raw.split(",") if part.strip()]
    normalized = [ext if ext.startswith(".") else f".{ext}" for ext in parsed]
    # Keep deterministic order while removing duplicates.
    return list(dict.fromkeys(normalized))


def _discover_batches(corpus_root: Path) -> list[Path]:
    return sorted(
        [entry.resolve() for entry in corpus_root.iterdir() if entry.is_dir()],
        key=lambda p: p.as_posix().lower(),
    )


def _load_state(state_file: Path, corpus_root: Path) -> IndexState:
    if not state_file.exists():
        return IndexState(
            corpus_root=str(corpus_root.resolve()),
            completed=set(),
            updated_at=_utc_now_iso(),
        )

    payload = json.loads(state_file.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid state payload (expected object): {state_file}")

    stored_root = str(payload.get("corpus_root", "")).strip()
    current_root = str(corpus_root.resolve())
    if stored_root and stored_root != current_root:
        raise ValueError(
            "State file corpus_root mismatch: "
            f"state={stored_root} current={current_root}. "
            "Use --reset-state to reinitialize."
        )

    completed_raw = payload.get("completed", [])
    if not isinstance(completed_raw, list):
        raise ValueError(
            f"Invalid state payload 'completed' (expected list): {state_file}"
        )

    completed = {
        str(Path(item).resolve())
        for item in completed_raw
        if isinstance(item, str) and item.strip()
    }
    return IndexState(
        corpus_root=current_root,
        completed=completed,
        updated_at=str(payload.get("updated_at", _utc_now_iso())),
    )


def _write_state(state_file: Path, state: IndexState) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "corpus_root": state.corpus_root,
        "completed": sorted(state.completed),
        "updated_at": state.updated_at,
    }
    state_file.write_text(
        json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
    )


def _index_batch(
    *,
    rag_url: str,
    directory: Path,
    extensions: list[str],
    force: bool,
) -> dict[str, Any]:
    body = {
        "path": str(directory) + "/",
        "extensions": extensions,
        "force": force,
    }
    with make_sync_client(url=rag_url, timeout=1800.0) as client:
        response = client.post("/index_directory", json=body)
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}


def main() -> None:
    args = _parse_args()
    if args.max_batches < 1:
        raise SystemExit("--max-batches must be >= 1")
    if args.sleep_seconds < 0:
        raise SystemExit("--sleep-seconds must be >= 0")

    corpus_root = args.corpus_root.expanduser().resolve()
    state_file = args.state_file.expanduser().resolve()
    extensions = _normalize_extensions(args.extensions)
    if not corpus_root.exists() or not corpus_root.is_dir():
        raise SystemExit(f"Corpus root not found or not a directory: {corpus_root}")
    if not extensions:
        raise SystemExit("No valid extensions configured.")

    state = _load_state(state_file, corpus_root)
    if args.reset_state:
        state = IndexState(
            corpus_root=str(corpus_root),
            completed=set(),
            updated_at=_utc_now_iso(),
        )
        _write_state(state_file, state)
        print(f"State reset: {state_file}")

    batches = _discover_batches(corpus_root)
    pending = [batch for batch in batches if str(batch) not in state.completed]

    print(f"Corpus root      : {corpus_root}")
    print(f"RAG URL          : {args.rag_url}")
    print(f"State file       : {state_file}")
    print(f"Extensions       : {', '.join(extensions)}")
    print(f"Discovered dirs  : {len(batches)}")
    print(f"Completed dirs   : {len(state.completed)}")
    print(f"Pending dirs     : {len(pending)}")

    if not pending:
        print("All discovered directories are already completed.")
        return

    planned = pending[: args.max_batches]
    print("\nPlanned this run:")
    for batch in planned:
        print(f"  - {batch}")
    if args.dry_run:
        return

    for index, batch in enumerate(planned, start=1):
        print(f"\n[{index}/{len(planned)}] Indexing: {batch}")
        started = time.monotonic()
        result = _index_batch(
            rag_url=args.rag_url,
            directory=batch,
            extensions=extensions,
            force=args.force,
        )
        elapsed = time.monotonic() - started

        indexed = int(result.get("indexed", 0))
        unchanged = int(result.get("unchanged", 0))
        deleted = int(result.get("deleted", 0))
        duplicates = int(result.get("duplicates", 0))
        files = result.get("files", [])
        files_count = len(files) if isinstance(files, list) else 0
        print(
            "  Result: "
            f"indexed={indexed} unchanged={unchanged} deleted={deleted} "
            f"duplicates={duplicates} files={files_count} elapsed={elapsed:.1f}s"
        )

        state.completed.add(str(batch))
        state.updated_at = _utc_now_iso()
        _write_state(state_file, state)

        if index < len(planned) and args.sleep_seconds > 0:
            time.sleep(args.sleep_seconds)

    print("\nRun complete.")
    print(f"Updated state at: {state.updated_at}")
    print(f"Remaining dirs  : {max(0, len(pending) - len(planned))}")


if __name__ == "__main__":
    main()
