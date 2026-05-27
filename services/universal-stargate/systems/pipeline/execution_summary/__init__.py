"""
Pipeline execution summary writer.

Captures detailed execution information for debugging and analysis:

- Input request
- All step outputs in execution order
- Final response
- Timing information
- Model metadata

Supports multiple output formats:

- Markdown: human-readable; available as a single file or as a per-step
  execution directory with one file per step plus a full summary.
- YAML: human + machine readable, structured.
- JSON: machine readable, compact.

Public API surface (consumers MUST import only these):

- :class:`ExecutionSummaryWriter` — the writer class.
- :func:`get_summary_writer` — factory that constructs a writer using the
  ``LOG_DIR`` environment variable.
"""

from __future__ import annotations

from .factory import get_summary_writer
from .writer import ExecutionSummaryWriter

__all__ = ["ExecutionSummaryWriter", "get_summary_writer"]
