"""Context bridge tools — structured access to tasks/ directory.

Provides read/write access to journal entries, discoveries, lessons,
and other workspace context files. The tasks/ directory is mounted
read-write at TASKS_ROOT (via tasks_path_policy).

Split into:
- journal_entries: list/read/write journal tools
- context_file_reads: list/read directory and files (text/binary)
- context_file_mutations: write/edit/move/delete with editable suffix policy

The public surface is unchanged: `from tools.context import register_context_tools`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .context_file_mutations import register_context_file_mutation_tools
from .context_file_reads import register_context_file_read_tools
from .journal_entries import register_journal_tools

if TYPE_CHECKING:
    from fastmcp import FastMCP


def register_context_tools(mcp: FastMCP) -> None:
    """Register context bridge tools on *mcp*."""
    register_journal_tools(mcp)
    register_context_file_read_tools(mcp)
    register_context_file_mutation_tools(mcp)


__all__ = ["register_context_tools"]
