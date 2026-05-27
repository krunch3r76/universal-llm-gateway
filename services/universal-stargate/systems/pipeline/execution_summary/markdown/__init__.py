"""
Internal markdown-rendering layer for ``execution_summary``.

Not part of the package's published API surface; ``writer.py`` is the sole
intra-package consumer. The two exported entry points are the rendering
endpoints used by ``ExecutionSummaryWriter``:

- ``render_full_summary_markdown`` — single source of truth for the inline-
  stepped full markdown summary (used by both ``write_summary_markdown`` and
  the ``full_summary.md`` file inside per-step execution directories).
- ``render_step_markdown`` — per-step markdown file inside per-step execution
  directories.

Helpers (``token_table``, ``verification``, ``aggregate``) compose into those
two endpoints; they are addressable by submodule path but are not re-exported.
"""

from __future__ import annotations

from .full_summary import render_full_summary_markdown
from .step_file import render_step_markdown

__all__ = ["render_full_summary_markdown", "render_step_markdown"]
