"""Pydantic schemas for Cortex API request/response payloads.

Historical monolithic ``models.py`` split into sibling submodules along
its section-comment boundaries (review-finding C10, session
claude-web-2026-05-13-0136). All public names are re-exported here so
``from cortex_store.models import X`` keeps working without changes to
any consumer module.

Submodule layout:
  ``_shared``        — validators + ``AssertionConfidence`` literal
  ``assertions``    — claim writes, reads, search, impact analysis
  ``relationships`` — typed edges between entities
  ``edges``         — session-scoped reasoning edges
  ``entities``      — entity CRUD + Card v0 read projection
  ``deadlines``     — matter deadline lists
  ``journals``      — session journals + atomic session close
  ``extraction``    — extraction-run lifecycle
  ``surface_forms`` — surface-form resolution cache
  ``reflective``    — reflective journal entries
  ``staging``       — extraction-staging proposals

Schema migration to structured types (list[str], etc.) was deferred as
intentional debt. API contract is string-passthrough at the boundary;
callers parse. Do not "fix" to strict types without a product decision
— see thread 045.
"""

from __future__ import annotations

from ._shared import (
    AssertionConfidence,
    reject_cortex_dropbox_source_uri,
    reject_cortex_dropbox_uri_list,
)
from .assertions import (
    ActionHint,
    AssertionCreate,
    AssertionCreateResponse,
    AssertionItem,
    AssertionList,
    AssertionListSummaryItem,
    AssertionUpdate,
    AssertionUpdateResponse,
    CompactionProjection,
    ContradictionConflict,
    DerivationType,
    EnrichRequest,
    EnrichResponse,
    ImpactAnalysisRequest,
    ImpactAnalysisResponse,
    NearDuplicateWarning,
    PredicateFormNormalize,
    ResolutionStatus,
    ReviewStatus,
    SupersedeRequest,
    SupersedeResponse,
    TouchedAssertionItem,
)
from .deadlines import DeadlineItem, DeadlineList
from .edges import EdgeCreate, EdgeItem, EdgeList, EdgeRetire, EdgeUpdate
from .entities import (
    CardAssertion,
    CardAssertionCounts,
    CardDebug,
    CardEdgeTypeCount,
    CardSection,
    CurrentStatus,
    EntityCard,
    EntityCreate,
    EntityDetail,
    EntityIntent,
    EntityList,
    EntityStatus,
    EntitySummary,
    EntityUpdate,
    RetentionPolicy,
    SupersededBreadcrumb,
    SupersededCorrection,
    WithheldStatusEntry,
)
from .extraction import (
    ExtractionCheckRequest,
    ExtractionCheckResponse,
    ExtractionRunComplete,
    ExtractionRunItem,
)
from .graph import (
    ActivatedAssertionItem,
    ActivateResponse,
    ImpactedEntityItem,
    ImpactResponse,
)
from .journals import (
    SessionCloseRequest,
    SessionCloseResponse,
    SessionHandoffUpsertRequest,
    SessionHandoffUpsertResponse,
    SessionJournalCreate,
    SessionJournalItem,
    SessionJournalList,
)
from .reflective import (
    ConsolidationData,
    JournalLinkCreate,
    JournalLinkItem,
    JournalLinkType,
    ReflectiveEntryCreate,
    ReflectiveEntryItem,
    ReflectiveEntryList,
    ReflectiveKind,
)
from .seat_claims import (
    SeatClaimEndReason,
    SeatClaimHolder,
    SeatClaimRequest,
    SeatClaimResponse,
    SeatClaimRow,
    SeatClaimStatus,
    SeatClaimsListResponse,
    SeatHeartbeatRequest,
    SeatHeartbeatResponse,
    SeatReleaseRequest,
    SeatReleaseResponse,
)
from .relationships import (
    RelationshipCreate,
    RelationshipCreateResponse,
    RelationshipDeleteResponse,
    RelationshipItem,
    RelationshipList,
    RelationshipUpdate,
)
from .search import (
    AssertionSearchItem,
    AssertionSearchResult,
    AssertionSearchSummaryItem,
)
from .staging import (
    ProposalAction,
    ProposalType,
    StagingApproval,
    StagingBatchApproval,
    StagingBatchCreate,
    StagingItem,
    StagingList,
    StagingProposalCreate,
    StagingStatus,
)
from .surface_forms import (
    SurfaceFormCacheResult,
    SurfaceFormCreate,
    SurfaceFormItem,
    SurfaceFormList,
)
from .terminal_facts import TerminalFactsBlock

# Legacy module-level helpers exposed for callers that previously imported
# the private ``_reject_cortex_dropbox_*`` functions directly. The public
# names live in ``._shared``; the underscored aliases are kept for one
# cycle so any out-of-tree caller doesn't break silently.
_reject_cortex_dropbox_source_uri = reject_cortex_dropbox_source_uri
_reject_cortex_dropbox_uri_list = reject_cortex_dropbox_uri_list

__all__ = [
    # _shared
    "AssertionConfidence",
    "reject_cortex_dropbox_source_uri",
    "reject_cortex_dropbox_uri_list",
    # assertions
    "ActionHint",
    "AssertionCreate",
    "AssertionCreateResponse",
    "AssertionItem",
    "AssertionList",
    "AssertionListSummaryItem",
    "AssertionUpdate",
    "AssertionUpdateResponse",
    "CompactionProjection",
    "ContradictionConflict",
    "DerivationType",
    "EnrichRequest",
    "EnrichResponse",
    "ImpactAnalysisRequest",
    "ImpactAnalysisResponse",
    "NearDuplicateWarning",
    "PredicateFormNormalize",
    "ResolutionStatus",
    "ReviewStatus",
    "AssertionSearchItem",
    "AssertionSearchResult",
    "AssertionSearchSummaryItem",
    "SupersedeRequest",
    "SupersedeResponse",
    "TouchedAssertionItem",
    # deadlines
    "DeadlineItem",
    "DeadlineList",
    # edges
    "EdgeCreate",
    "EdgeItem",
    "EdgeList",
    "EdgeRetire",
    "EdgeUpdate",
    # entities
    "CardAssertion",
    "CardAssertionCounts",
    "CardDebug",
    "CardEdgeTypeCount",
    "CardSection",
    "CurrentStatus",
    "EntityCard",
    "EntityCreate",
    "EntityDetail",
    "EntityIntent",
    "EntityList",
    "EntityStatus",
    "EntitySummary",
    "EntityUpdate",
    "RetentionPolicy",
    # extraction
    "ExtractionCheckRequest",
    "ExtractionCheckResponse",
    "ExtractionRunComplete",
    "ExtractionRunItem",
    # graph traversal
    "ActivatedAssertionItem",
    "ActivateResponse",
    "ImpactedEntityItem",
    "ImpactResponse",
    # journals
    "SessionCloseRequest",
    "SessionCloseResponse",
    "SessionHandoffUpsertRequest",
    "SessionHandoffUpsertResponse",
    "SessionJournalCreate",
    "SessionJournalItem",
    "SessionJournalList",
    # reflective
    "ConsolidationData",
    "JournalLinkCreate",
    "JournalLinkItem",
    "JournalLinkType",
    "ReflectiveEntryCreate",
    "ReflectiveEntryItem",
    "ReflectiveEntryList",
    "ReflectiveKind",
    # seat_claims
    "SeatClaimEndReason",
    "SeatClaimHolder",
    "SeatClaimRequest",
    "SeatClaimResponse",
    "SeatClaimRow",
    "SeatClaimStatus",
    "SeatClaimsListResponse",
    "SeatHeartbeatRequest",
    "SeatHeartbeatResponse",
    "SeatReleaseRequest",
    "SeatReleaseResponse",
    # relationships
    "RelationshipCreate",
    "RelationshipCreateResponse",
    "RelationshipDeleteResponse",
    "RelationshipItem",
    "RelationshipList",
    "RelationshipUpdate",
    # staging
    "ProposalAction",
    "ProposalType",
    "StagingApproval",
    "StagingBatchApproval",
    "StagingBatchCreate",
    "StagingItem",
    "StagingList",
    "StagingProposalCreate",
    "StagingStatus",
    # surface_forms
    "SurfaceFormCacheResult",
    "SurfaceFormCreate",
    "SurfaceFormItem",
    "SurfaceFormList",
    # terminal_facts
    "TerminalFactsBlock",
]
