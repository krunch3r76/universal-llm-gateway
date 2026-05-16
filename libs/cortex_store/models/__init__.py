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
  ``chunks``        — chunk CRUD
  ``ingest``        — document ingest + assert-from-chunk
  ``extraction``    — extraction-run lifecycle
  ``surface_forms`` — surface-form resolution cache
  ``reflective``    — reflective journal entries

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
    AssertionSearchItem,
    AssertionSearchResult,
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
from .chunks import ChunkCreate, ChunkItem, ChunkList
from .deadlines import DeadlineItem, DeadlineList
from .edges import EdgeCreate, EdgeItem, EdgeList, EdgeRetire
from .entities import (
    CardAssertion,
    CardDebug,
    CardEdgeTypeCount,
    CardSection,
    EntityCard,
    EntityCreate,
    EntityDetail,
    EntityIntent,
    EntityList,
    EntityStatus,
    EntitySummary,
    EntityUpdate,
    RetentionPolicy,
)
from .extraction import (
    ExtractionCheckRequest,
    ExtractionCheckResponse,
    ExtractionRunComplete,
    ExtractionRunItem,
)
from .ingest import (
    AssertFromChunkRequest,
    AssertFromChunkResponse,
    ChunkResult,
    IngestDocumentRequest,
    IngestDocumentResponse,
)
from .journals import (
    SessionCloseRequest,
    SessionCloseResponse,
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
from .relationships import (
    RelationshipCreate,
    RelationshipCreateResponse,
    RelationshipDeleteResponse,
    RelationshipItem,
    RelationshipList,
    RelationshipUpdate,
)
from .surface_forms import (
    SurfaceFormCacheResult,
    SurfaceFormCreate,
    SurfaceFormItem,
    SurfaceFormList,
)

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
    "AssertionSearchItem",
    "AssertionSearchResult",
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
    "SupersedeRequest",
    "SupersedeResponse",
    "TouchedAssertionItem",
    # chunks
    "ChunkCreate",
    "ChunkItem",
    "ChunkList",
    # deadlines
    "DeadlineItem",
    "DeadlineList",
    # edges
    "EdgeCreate",
    "EdgeItem",
    "EdgeList",
    "EdgeRetire",
    # entities
    "CardAssertion",
    "CardDebug",
    "CardEdgeTypeCount",
    "CardSection",
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
    # ingest
    "AssertFromChunkRequest",
    "AssertFromChunkResponse",
    "ChunkResult",
    "IngestDocumentRequest",
    "IngestDocumentResponse",
    # journals
    "SessionCloseRequest",
    "SessionCloseResponse",
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
    # relationships
    "RelationshipCreate",
    "RelationshipCreateResponse",
    "RelationshipDeleteResponse",
    "RelationshipItem",
    "RelationshipList",
    "RelationshipUpdate",
    # surface_forms
    "SurfaceFormCacheResult",
    "SurfaceFormCreate",
    "SurfaceFormItem",
    "SurfaceFormList",
]
