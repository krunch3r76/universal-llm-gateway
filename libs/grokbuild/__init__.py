"""grokbuild — workspace-shared library extracted from services/mcp-server/tools/.

Re-exports the public surface consumed by the MCP tool shell (grokbuild.py)
and future worker processes (Phase A.3/B).
"""

from __future__ import annotations

from grokbuild.constants import MODEL_REGISTRY
from grokbuild.dispatch import dispatch_op
from grokbuild.events import emit_grok_build_dispatch_rejected
from grokbuild.fetch_result import fetch_result_op
from grokbuild.git_ops import pr_create_op, push_op
from grokbuild.runner import RunnerResult, RunnerSpec
from grokbuild.validator import validate_dispatch
from grokbuild.worktree import worktree_create_op
from grokbuild.worktree_list import worktree_list_op
from grokbuild.worktree_remove import worktree_remove_op

__all__ = [
    "MODEL_REGISTRY",
    "dispatch_op",
    "emit_grok_build_dispatch_rejected",
    "fetch_result_op",
    "pr_create_op",
    "push_op",
    "RunnerResult",
    "RunnerSpec",
    "validate_dispatch",
    "worktree_create_op",
    "worktree_list_op",
    "worktree_remove_op",
]
