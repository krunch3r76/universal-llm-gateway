"""Package re-exports for step, fallback, consensus, and RAG bus event factories.

Re-exports all 29 @event_factory symbols so callers using
``from ...events.step import X`` or ``from systems.pipeline.core.events.step import X``
require no import-path changes after the package-shadow split.
"""

from .consensus import (
    CombinePassagesCompleted,
    CoverageAuditCompleted,
    OrganizeFactsCompleted,
)
from .lifecycle import (
    StepCompleted,
    StepConditionEvaluated,
    StepContextExceeded,
    StepFailed,
    StepModelResolved,
    StepSkipped,
    StepStarted,
    SubPipelineExpanded,
)
from .model_fallback import (
    StepModelFallback,
    StepModelFallbackSuppressed,
)
from .rag_postprocess import (
    RagCoverageSelectionApplied,
    RagGenerationContextRefined,
    RagHintsFiltered,
    RagMetadataBoostApplied,
    RagNeighborExpansionApplied,
    RagRerankCompleted,
)
from .rag_query import (
    RagQueryAnalysisCompleted,
    RagQueryRewriteCompleted,
    RagQueryRewriteSkipped,
)
from .rag_retrieval import (
    RagRetrievalBibliographyFiltered,
    RagRetrievalCompleted,
    RagRetrievalFailed,
    RagRetrievalParamsResolved,
    RagRetrievalSkipped,
    RagRetrievalSourceDiversityLimited,
    RagScopeRejected,
)

__all__ = [
    # Step lifecycle
    "StepStarted",
    "StepModelResolved",
    "StepCompleted",
    "StepFailed",
    "StepContextExceeded",
    "StepSkipped",
    "StepConditionEvaluated",
    "SubPipelineExpanded",
    # Model fallback
    "StepModelFallback",
    "StepModelFallbackSuppressed",
    # Consensus bus
    "CoverageAuditCompleted",
    "OrganizeFactsCompleted",
    "CombinePassagesCompleted",
    # RAG retrieval
    "RagRetrievalParamsResolved",
    "RagRetrievalCompleted",
    "RagRetrievalFailed",
    "RagRetrievalSkipped",
    "RagRetrievalBibliographyFiltered",
    "RagRetrievalSourceDiversityLimited",
    "RagScopeRejected",
    # RAG query
    "RagQueryAnalysisCompleted",
    "RagQueryRewriteCompleted",
    "RagQueryRewriteSkipped",
    # RAG post-process
    "RagNeighborExpansionApplied",
    "RagCoverageSelectionApplied",
    "RagMetadataBoostApplied",
    "RagRerankCompleted",
    "RagHintsFiltered",
    "RagGenerationContextRefined",
]
