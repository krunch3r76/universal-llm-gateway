"""Ingest + assert-from-chunk Pydantic models.

Carries the spec § 2.2 / § 3.2 ``authority_class`` field plus the
review-finding-C4 ``model_validator`` that enforces canonical
``cortex://`` source_uri when authority_class is set.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator

from ._shared import (
    reject_cortex_dropbox_source_uri,
    reject_cortex_dropbox_uri_list,
)
from .assertions import AssertionItem


class IngestDocumentRequest(BaseModel):
    source_uri: str
    content: str
    observer: str = "web"
    source_date: str | None = None
    # Spec § 3.2 — selects the structurally-aware chunker. None falls
    # back to the paragraph-boundary default; unrecognized values also
    # fall back so the surface is forward-compatible with Phase-2 classes.
    authority_class: str | None = None

    _validate_source_uri = field_validator("source_uri")(
        reject_cortex_dropbox_source_uri
    )

    @model_validator(mode="after")
    def _require_cortex_uri_when_authority_class_set(self) -> IngestDocumentRequest:
        """Enforce canonical source_uri when an authority_class is provided.

        Spec § 2.2 / § 3.2: pinpoint resolution keys on
        ``chunks.source_uri = cortex://<entity_id>``. If an
        ``authority_class``-aware ingest writes chunks under a non-cortex
        URI (workspace path, https URL), the resolver's fragment lookup
        will never find them and the verbatim-enforcement gate (§ 6) is
        silently unenforceable. Reject at the request boundary instead.

        Callers that want raw paragraph chunking without pinpoint
        addressability can omit ``authority_class`` — they keep the
        pre-spec contract (chunks addressable by ``chunk_id`` only).
        """
        if self.authority_class is not None and not self.source_uri.startswith(
            "cortex://"
        ):
            raise ValueError(
                "source_uri must be a cortex:// URI when authority_class is "
                "set — the resolver fragment lookup (spec § 2.2) keys on the "
                "canonical cortex://<entity_id> form. Got: "
                f"{self.source_uri!r}, authority_class={self.authority_class!r}."
            )
        return self


class ChunkResult(BaseModel):
    chunk_id: int
    chunk_index: int
    snippet: str = Field(description="First 200 chars of chunk content")
    extracted_dates: list[str]
    token_count: int
    # Spec § 2.2 — dotted-path subdivision label that becomes the URI
    # fragment under cortex://...#<pinpoint>. None for default-chunker chunks.
    pinpoint: str | None = None


class IngestDocumentResponse(BaseModel):
    source_uri: str
    chunk_count: int
    chunks: list[ChunkResult]


class AssertFromChunkRequest(BaseModel):
    chunk_id: int
    entity_id: str
    claim: str
    confidence: str
    evidence: str
    evidence_uris: list[str] | None = None
    derivation_type: str | None = None
    confidence_score: float | None = None
    observed_at: str | None = None
    valid_from: str | None = None
    reasoning_summary: str | None = None
    resolution_status: str | None = None
    seeded_by: str | None = None

    _validate_evidence_uris = field_validator("evidence_uris")(
        reject_cortex_dropbox_uri_list
    )


class AssertFromChunkResponse(BaseModel):
    item: AssertionItem
    was_new: bool
    suggested_valid_from: str | None = None
    quality_score: float
    validation_warnings: list[dict[str, str]] | None = None
