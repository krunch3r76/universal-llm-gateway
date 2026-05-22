"""Re-export facade for all grokbuild event factories and emitters.

Sub-modules:
  grokbuild.events_dispatch  — dispatch lifecycle (called/completed/failed/timeout/rejected)
  grokbuild.events_create    — worktree-create lifecycle
  grokbuild.events_worktree  — worktree remove / list / registry events
  grokbuild.events_core      — shared ``_emit`` helper + ``record`` (monkeypatch target)
"""

from __future__ import annotations

from grokbuild.events_core import (
    _emit,  # noqa: F401 — re-exported
    record,  # noqa: F401 — monkeypatch target for tests
)
from grokbuild.events_create import (
    GrokBuildCreateCalled,
    GrokBuildCreateCompleted,
    GrokBuildCreateFailed,
    GrokBuildCreateRejected,
    emit_grok_build_create_called,
    emit_grok_build_create_completed,
    emit_grok_build_create_failed,
    emit_grok_build_create_rejected,
)
from grokbuild.events_dispatch import (
    GrokBuildDispatchCalled,
    GrokBuildDispatchCompleted,
    GrokBuildDispatchFailed,
    GrokBuildDispatchRejected,
    GrokBuildDispatchTimeout,
    emit_grok_build_dispatch_called,
    emit_grok_build_dispatch_completed,
    emit_grok_build_dispatch_failed,
    emit_grok_build_dispatch_rejected,
    emit_grok_build_dispatch_timeout,
)
from grokbuild.events_worktree import (
    GrokBuildListCalled,
    GrokBuildListCompleted,
    GrokBuildListFailed,
    GrokBuildRegistryRecovered,
    GrokBuildRemoveCalled,
    GrokBuildRemoveCompleted,
    GrokBuildRemoveFailed,
    GrokBuildRemoveRejected,
    emit_grok_build_list_called,
    emit_grok_build_list_completed,
    emit_grok_build_list_failed,
    emit_grok_build_registry_recovered,
    emit_grok_build_remove_called,
    emit_grok_build_remove_completed,
    emit_grok_build_remove_failed,
    emit_grok_build_remove_rejected,
)

__all__ = [
    "_emit",
    "record",
    "GrokBuildDispatchCalled",
    "GrokBuildDispatchCompleted",
    "GrokBuildDispatchFailed",
    "GrokBuildDispatchTimeout",
    "GrokBuildDispatchRejected",
    "emit_grok_build_dispatch_called",
    "emit_grok_build_dispatch_completed",
    "emit_grok_build_dispatch_failed",
    "emit_grok_build_dispatch_timeout",
    "emit_grok_build_dispatch_rejected",
    "GrokBuildCreateCalled",
    "GrokBuildCreateCompleted",
    "GrokBuildCreateFailed",
    "GrokBuildCreateRejected",
    "emit_grok_build_create_called",
    "emit_grok_build_create_completed",
    "emit_grok_build_create_failed",
    "emit_grok_build_create_rejected",
    "GrokBuildRemoveCalled",
    "GrokBuildRemoveCompleted",
    "GrokBuildRemoveFailed",
    "GrokBuildRemoveRejected",
    "GrokBuildListCalled",
    "GrokBuildListCompleted",
    "GrokBuildListFailed",
    "GrokBuildRegistryRecovered",
    "emit_grok_build_remove_called",
    "emit_grok_build_remove_completed",
    "emit_grok_build_remove_failed",
    "emit_grok_build_remove_rejected",
    "emit_grok_build_list_called",
    "emit_grok_build_list_completed",
    "emit_grok_build_list_failed",
    "emit_grok_build_registry_recovered",
]
