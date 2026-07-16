"""
`nonstreaming/executor` package — request execution for the nonstreaming proxy.

Public API (preserves all existing import paths):

    from systems.proxy.core.nonstreaming.executor import RequestExecutor

Module layout:
    core.py                    — RequestExecutor orchestrator
    federated_execution.py     — dispatch: circuit-breaker, body prep, path selection
    federated_streaming.py     — SSE stream forwarding
    federated_pseudostream.py  — upstream SSE + master accumulate → JSON
    federated_nonstreaming.py  — JSON response forwarding + tracker/forwarder dispatch
    embeddings.py              — embedding request execution
    token_capping.py           — max_tokens per-slot safety cap
"""

from .core import RequestExecutor
from .embeddings import execute_embedding_request, forward_embedding_request
from .federated_execution import execute_federated_request
from .federated_nonstreaming import (
    _execute_federated_nonstreaming,
    _forward_via_tracker_or_forwarder,
)
from .federated_pseudostream import _execute_federated_pseudostream
from .federated_streaming import _execute_federated_streaming
from .token_capping import _cap_max_tokens_to_slot_context

__all__ = [
    "RequestExecutor",
    "execute_federated_request",
    "_execute_federated_streaming",
    "_execute_federated_pseudostream",
    "_execute_federated_nonstreaming",
    "_forward_via_tracker_or_forwarder",
    "_cap_max_tokens_to_slot_context",
    "execute_embedding_request",
    "forward_embedding_request",
]
