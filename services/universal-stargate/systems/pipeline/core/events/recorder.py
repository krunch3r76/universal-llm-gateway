"""
Event recorder: persists pipeline events to per-execution JSONL files.

The recorder is the single write path for pipeline observability events.
It auto-populates identity fields (pipeline_id, execution_id), timestamps
(monotonic + wall clock), and sequence numbers before writing.

Usage:
    recorder = EventRecorder(pipeline_id="my-pipeline", execution_id="abc123",
                             output_dir=Path("/tmp/logs/.../abc123"))
    recorder.emit(StepStarted(step_name="analyze", step_type="generate"))
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .base import PipelineEvent

logger = get_logger(__name__)


class EventRecorder:
    """
    Write pipeline events to a per-execution JSONL file.

    Thread-safe via a lock on the sequence counter and file writes.
    The JSONL file is opened on first emit and closed explicitly or on GC.
    """

    def __init__(
        self,
        *,
        pipeline_id: str,
        execution_id: str,
        output_dir: Path,
    ) -> None:
        self._pipeline_id = pipeline_id
        self._execution_id = execution_id
        self._output_dir = output_dir
        self._start_mono = time.monotonic()
        self._sequence = 0
        self._lock = threading.Lock()
        self._file = None
        self._closed = False

    def _ensure_open(self) -> None:
        """Create output directory and open JSONL file on first write."""
        if self._file is not None:
            return
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._file = (self._output_dir / "events.jsonl").open("a", encoding="utf-8")

    def emit(self, event: PipelineEvent) -> None:
        """
        Record an event: populate identity/timing fields, write to JSONL.

        Auto-populates: pipeline_id, execution_id, event_type,
        timestamp_ms, wall_clock, sequence.
        """
        if self._closed:
            logger.warning("EventRecorder closed, dropping event: %s", event.event_type)
            return

        with self._lock:
            # Auto-populate recorder-managed fields
            event.pipeline_id = self._pipeline_id
            event.execution_id = self._execution_id
            event.timestamp_ms = round((time.monotonic() - self._start_mono) * 1000, 2)
            event.wall_clock = datetime.now(UTC).isoformat()
            event.sequence = self._sequence
            self._sequence += 1

            # Write JSONL line
            self._ensure_open()
            assert self._file is not None
            line = json.dumps(event.to_dict(), default=str, separators=(",", ":"))
            self._file.write(line + "\n")
            self._file.flush()

    def close(self) -> None:
        """Flush and close the JSONL file."""
        self._closed = True
        if self._file is not None:
            self._file.flush()
            self._file.close()
            self._file = None

    @property
    def output_path(self) -> Path:
        """Path to the events.jsonl file."""
        return self._output_dir / "events.jsonl"

    def __del__(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
