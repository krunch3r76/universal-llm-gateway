"""Datatypes for provenance reconstruct pass."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Candidate:
    id: int
    entity_id: str
    claim: str
    evidence: str
    evidence_uris: list[str]
    chunk_id: str | None
    derivation_type: str
    confidence: str


@dataclass
class Outcome:
    assertion_id: int
    entity_id: str
    action: str  # attach | flag | skip
    detail: str
    resolved_uri: str | None = None
    near_miss: str | None = None
