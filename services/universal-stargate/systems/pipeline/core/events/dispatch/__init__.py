"""Package re-exports for async-dispatch tracker and frontier-dispatch event factories.

Re-exports all 24 @event_factory symbols so callers using
``from ..events.dispatch import X`` or
``from systems.pipeline.core.events.dispatch import X``
require no import-path changes after the package-shadow split.

Two signal namespaces:
- ``pipeline.dispatch.*``           — async tracker admission, terminal state, journal
- ``pipeline.frontier.dispatch.*``  — native tool loop, remote MCP, anomaly detection

Invariants (moved verbatim from the prior monolith):
- ∀ async dispatch accepted        ⟹ emit ``pipeline.dispatch.async``
- ∀ tracker terminal transition    ⟹ emit ``pipeline.dispatch.completed``
- ∀ admission refused              ⟹ emit ``pipeline.dispatch.rejected``
- ∀ terminal record TTL-pruned     ⟹ emit ``pipeline.dispatch.tracker.expired``
- ∀ journal write/read/prune       ⟹ emit ``pipeline.dispatch.journal.*``
- ∀ frontier_dispatch_v1 step exec ⟹ emit ``pipeline.frontier.dispatch.*``
"""

from .async_tracker import (
    PipelineDispatchAsync,
    PipelineDispatchCancelled,
    PipelineDispatchCompleted,
    PipelineDispatchRejected,
    PipelineDispatchTrackerExpired,
)
from .frontier_anomaly import (
    PipelineFrontierDispatchAgentModelMismatch,
    PipelineFrontierDispatchOutputShort,
    PipelineFrontierDispatchTerminationShadow,
)
from .frontier_lifecycle import (
    PipelineFrontierDispatchCompleted,
    PipelineFrontierDispatchEmptyCompletion,
    PipelineFrontierDispatchExhausted,
    PipelineFrontierDispatchHydrated,
    PipelineFrontierDispatchStarted,
)
from .frontier_mcp import (
    PipelineFrontierDispatchRemoteMcpEnabled,
    PipelineFrontierDispatchRemoteMcpMisconfigured,
    PipelineFrontierDispatchRemoteMcpUnsupported,
)
from .frontier_tools import (
    PipelineFrontierDispatchToolCalled,
    PipelineFrontierDispatchToolFailed,
    PipelineFrontierDispatchToolListSupplied,
    PipelineFrontierDispatchToolRequested,
    PipelineFrontierDispatchToolSuppressed,
)
from .journal import (
    PipelineDispatchJournalPruned,
    PipelineDispatchJournalRead,
    PipelineDispatchJournalWritten,
)

__all__ = [
    # Async tracker (pipeline.dispatch.*)
    "PipelineDispatchAsync",
    "PipelineDispatchCompleted",
    "PipelineDispatchCancelled",
    "PipelineDispatchRejected",
    "PipelineDispatchTrackerExpired",
    # Journal (pipeline.dispatch.journal.*)
    "PipelineDispatchJournalWritten",
    "PipelineDispatchJournalRead",
    "PipelineDispatchJournalPruned",
    # Frontier lifecycle
    "PipelineFrontierDispatchHydrated",
    "PipelineFrontierDispatchStarted",
    "PipelineFrontierDispatchCompleted",
    "PipelineFrontierDispatchEmptyCompletion",
    "PipelineFrontierDispatchExhausted",
    # Frontier tools
    "PipelineFrontierDispatchToolRequested",
    "PipelineFrontierDispatchToolCalled",
    "PipelineFrontierDispatchToolFailed",
    "PipelineFrontierDispatchToolSuppressed",
    "PipelineFrontierDispatchToolListSupplied",
    # Frontier remote MCP
    "PipelineFrontierDispatchRemoteMcpEnabled",
    "PipelineFrontierDispatchRemoteMcpMisconfigured",
    "PipelineFrontierDispatchRemoteMcpUnsupported",
    # Frontier anomaly
    "PipelineFrontierDispatchOutputShort",
    "PipelineFrontierDispatchTerminationShadow",
    "PipelineFrontierDispatchAgentModelMismatch",
]
