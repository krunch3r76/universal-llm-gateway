"""Incremental in-memory cache for the execution list API.

Maintains an index keyed by pipeline/exec directory so steady-state polls
avoid rebuilding every list item and re-tailing events.jsonl for complete runs.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aggregator import _build_summary, _dir_timestamp_to_utc

logger = logging.getLogger(__name__)

_LIST_QUESTION_PREVIEW_CHARS = 220
_STALE_THRESHOLD_SECONDS = 5 * 60
_DEFAULT_FULL_RESCAN_SECONDS = 60.0


def build_execution_list_item(
    exec_dir: Path, pipeline_id_fallback: str
) -> dict[str, Any]:
    """Build lightweight metadata for execution cards."""
    first = _read_first_event(exec_dir / "events.jsonl")

    dir_name = exec_dir.name
    parts = dir_name.split("_", maxsplit=2)
    timestamp = "_".join(parts[:2]) if len(parts) >= 2 else dir_name
    execution_suffix = parts[2] if len(parts) >= 3 else dir_name

    pipeline_id = first.get("pipeline_id") or pipeline_id_fallback
    execution_id = first.get("execution_id") or execution_suffix
    started_at_utc = first.get("wall_clock") or _dir_timestamp_to_utc(dir_name)
    question = _to_question_preview(first.get("source_text", "Unknown question"))
    step_count = int(first.get("step_count", 0) or 0)

    return {
        "pipeline_id": pipeline_id,
        "execution_id": execution_id,
        "timestamp": timestamp,
        "started_at_utc": started_at_utc,
        "question": question,
        "step_count": step_count,
        "active_calls": [],
        "failed_calls": [],
        "summary": _build_summary([]),
        "is_live": not is_execution_complete(exec_dir),
    }


def _read_first_event(path: Path) -> dict[str, Any]:
    """Read only the first JSONL event from an execution file."""
    try:
        with path.open("r", encoding="utf-8") as fh:
            first_line = fh.readline().strip()
    except OSError:
        return {}

    if not first_line:
        return {}
    return json.loads(first_line)


def _to_question_preview(text: str) -> str:
    """Collapse whitespace and clamp very long source_text for list cards."""
    compact = " ".join(text.split())
    if len(compact) <= _LIST_QUESTION_PREVIEW_CHARS:
        return compact
    return f"{compact[:_LIST_QUESTION_PREVIEW_CHARS].rstrip()}..."


def is_execution_complete(exec_dir: Path) -> bool:
    """Check whether the execution has a terminal event or is stale."""
    events_file = exec_dir / "events.jsonl"
    if not events_file.exists():
        return False

    terminal_types = {"pipeline_completed", "pipeline_failed", "pipeline_cancelled"}
    chunk_size = 8192
    try:
        stat = events_file.stat()
        mtime = stat.st_mtime
        size = stat.st_size

        if size == 0:
            return False

        with events_file.open("rb") as f:
            read_size = min(size, chunk_size * 4)
            _ = f.seek(max(0, size - read_size), os.SEEK_SET)
            raw = f.read().decode("utf-8", errors="ignore")

        ev = _last_parseable_event(raw)
        if ev is None and size > read_size:
            ev = _last_parseable_event(events_file.read_text(encoding="utf-8"))
        if ev is not None:
            if ev.get("event_type", "") in terminal_types:
                return True
            age_seconds = time.time() - mtime
            if age_seconds > _STALE_THRESHOLD_SECONDS:
                logger.info(
                    "Marking %s as complete: no terminal event and file "
                    "unmodified for %.0fs",
                    exec_dir.name,
                    age_seconds,
                )
                return True
            return False
    except OSError as exc:
        logger.warning("Could not check completion for %s: %s", exec_dir, exc)

    return False


def _last_parseable_event(raw: str) -> dict[str, Any] | None:
    """Return the last parseable JSON event from raw JSONL content."""
    for raw_line in reversed(raw.splitlines()):
        line = raw_line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


@dataclass
class _CachedEntry:
    list_item: dict[str, Any]
    events_mtime: float
    events_size: int


@dataclass
class _PipelineSnapshot:
    mtime: float
    exec_names: frozenset[str]


class ExecutionListCache:
    """L1 incremental index with completion memo keyed on events (mtime, size)."""

    def __init__(self, *, full_rescan_interval: float = _DEFAULT_FULL_RESCAN_SECONDS) -> None:
        self._entries: dict[str, _CachedEntry] = {}
        self._pipeline_snapshots: dict[str, _PipelineSnapshot] = {}
        self._sorted_executions: list[dict[str, Any]] | None = None
        self._last_full_scan: float = 0.0
        self._version: int = 0
        self._full_rescan_interval = full_rescan_interval

    @property
    def version(self) -> int:
        return self._version

    def refresh(self, summaries_dir: Path) -> list[dict[str, Any]]:
        """Rescan the summaries tree, reusing cached rows when fingerprints match."""
        summaries_dir = Path(summaries_dir)
        now = time.time()
        force_full = (now - self._last_full_scan) >= self._full_rescan_interval
        if force_full:
            self._last_full_scan = now

        if not summaries_dir.exists():
            if self._entries:
                self._entries.clear()
                self._pipeline_snapshots.clear()
                self._sorted_executions = None
                self._version += 1
            return []

        seen_keys: set[str] = set()
        mutated = False

        for pipeline_dir in summaries_dir.iterdir():
            if not pipeline_dir.is_dir():
                continue
            pipeline_id = pipeline_dir.name
            exec_names = self._pipeline_exec_names(pipeline_dir, pipeline_id, force_full)
            fast_path = not force_full and self._pipeline_snapshot_current(
                pipeline_dir, pipeline_id, exec_names
            )
            if fast_path and any(
                f"{pipeline_id}/{name}" not in self._entries for name in exec_names
            ):
                fast_path = False
            if fast_path:
                for exec_name in exec_names:
                    seen_keys.add(f"{pipeline_id}/{exec_name}")
                for exec_name in exec_names:
                    key = f"{pipeline_id}/{exec_name}"
                    cached = self._entries.get(key)
                    if cached is None or not cached.list_item.get("is_live"):
                        continue
                    if self._refresh_live_entry(
                        pipeline_dir / exec_name, pipeline_id, key, force_full
                    ):
                        mutated = True
                continue

            for exec_name in exec_names:
                key = f"{pipeline_id}/{exec_name}"
                seen_keys.add(key)
                if self._refresh_exec_entry(
                    pipeline_dir / exec_name, pipeline_id, key, force_full
                ):
                    mutated = True

        removed = set(self._entries.keys()) - seen_keys
        for key in removed:
            del self._entries[key]
            mutated = True

        if mutated:
            self._version += 1
            self._sorted_executions = None

        if self._sorted_executions is not None:
            return self._sorted_executions

        executions = [entry.list_item for entry in self._entries.values()]
        executions.sort(
            key=lambda item: item.get("started_at_utc") or item["timestamp"],
            reverse=True,
        )
        self._sorted_executions = executions
        return executions

    def _pipeline_snapshot_current(
        self,
        pipeline_dir: Path,
        pipeline_id: str,
        exec_names: frozenset[str],
    ) -> bool:
        snapshot = self._pipeline_snapshots.get(pipeline_id)
        if snapshot is None or snapshot.exec_names != exec_names:
            return False
        try:
            return snapshot.mtime == pipeline_dir.stat().st_mtime
        except OSError:
            return False

    def _refresh_exec_entry(
        self,
        exec_dir: Path,
        pipeline_id: str,
        key: str,
        force_full: bool,
    ) -> bool:
        events_file = exec_dir / "events.jsonl"
        if not events_file.exists():
            return False

        cached = self._entries.get(key)
        if not force_full and cached is not None and not cached.list_item.get("is_live"):
            return False

        try:
            ev_stat = events_file.stat()
            events_mtime = ev_stat.st_mtime
            events_size = ev_stat.st_size
        except OSError as exc:
            logger.warning("Could not stat %s: %s", events_file, exc)
            return False

        if (
            not force_full
            and cached is not None
            and cached.events_mtime == events_mtime
            and cached.events_size == events_size
        ):
            return self._refresh_live_entry(exec_dir, pipeline_id, key, force_full)

        try:
            list_item = build_execution_list_item(exec_dir, pipeline_id)
        except Exception as exc:
            logger.error("Failed to build list item for %s: %s", exec_dir, exc)
            return False

        self._entries[key] = _CachedEntry(
            list_item=list_item,
            events_mtime=events_mtime,
            events_size=events_size,
        )
        return True

    def _refresh_live_entry(
        self,
        exec_dir: Path,
        pipeline_id: str,
        key: str,
        force_full: bool,
    ) -> bool:
        cached = self._entries.get(key)
        if cached is None or not cached.list_item.get("is_live"):
            return False

        events_file = exec_dir / "events.jsonl"
        try:
            ev_stat = events_file.stat()
        except OSError as exc:
            logger.warning("Could not stat %s: %s", events_file, exc)
            return False

        if (
            not force_full
            and cached.events_mtime == ev_stat.st_mtime
            and cached.events_size == ev_stat.st_size
        ):
            if is_execution_complete(exec_dir):
                cached.list_item = {**cached.list_item, "is_live": False}
                return True
            return False

        return self._refresh_exec_entry(exec_dir, pipeline_id, key, force_full=True)

    def _pipeline_exec_names(
        self,
        pipeline_dir: Path,
        pipeline_id: str,
        force_full: bool,
    ) -> frozenset[str]:
        """Return exec dir names, reusing a snapshot when the pipeline dir is unchanged."""
        try:
            pipeline_mtime = pipeline_dir.stat().st_mtime
        except OSError as exc:
            logger.warning("Could not stat pipeline dir %s: %s", pipeline_dir, exc)
            return frozenset()

        snapshot = self._pipeline_snapshots.get(pipeline_id)
        if not force_full and snapshot is not None and snapshot.mtime == pipeline_mtime:
            return snapshot.exec_names

        exec_names = frozenset(
            child.name for child in pipeline_dir.iterdir() if child.is_dir()
        )
        self._pipeline_snapshots[pipeline_id] = _PipelineSnapshot(
            mtime=pipeline_mtime,
            exec_names=exec_names,
        )
        return exec_names
