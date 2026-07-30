"""ImplementCloseout v1 pydantic models — no adapter imports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from implement_admission.propagation_row import PropagationRow
from implement_admission.spec import CloseoutStatus

CoverageStatus = Literal["complete", "partial", "unavailable"]

AmbientRepoCause = Literal[
    "ambient:concurrent_commit",
    "ambient:concurrent_edit",
    "ambient:vanished",
    "declared_unproved",
]


class AmbientRepoMovement(BaseModel):
    path: str
    cause: AmbientRepoCause


class EffectEntry(BaseModel):
    op: str
    target: str | None = None
    detail: dict[str, Any] | None = None
    identity: str | None = None


class SurfaceSection(BaseModel):
    surface: str
    source: str
    entries: list[EffectEntry] = Field(default_factory=list)
    cross_check: str | None = None


class EffectsManifest(BaseModel):
    schema_version: int = 1
    dispatch_id: str
    thread_id: str
    capture_sources: list[str] = Field(default_factory=list)
    surfaces: dict[str, SurfaceSection] = Field(default_factory=dict)
    coverage: dict[str, CoverageStatus] = Field(default_factory=dict)
    external_effects: Literal["scoped_out"] = "scoped_out"


class Verification(BaseModel):
    command: str
    exit_code: int


class EvidenceUris(BaseModel):
    dispatch_ids: list[str] = Field(default_factory=list)
    bus_threads: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    cortex_assertions: list[str] | None = Field(default_factory=list)
    git_refs: list[str] = Field(default_factory=list)


class AdapterResult(BaseModel):
    adapter: str
    status: str
    mutation: str | None = None
    error: str | None = None


class ImplementCloseout(BaseModel):
    schema_version: Literal[1] = 1
    status: CloseoutStatus
    summary: str
    deviations: list[str] = Field(default_factory=list)
    files_created: list[str] = Field(default_factory=list)
    files_modified: list[str] = Field(default_factory=list)
    files_deleted: list[str] = Field(default_factory=list)
    files_ambient_repo_movement: list[AmbientRepoMovement] = Field(default_factory=list)
    capture_status: Literal["complete", "partial", "unavailable"] | None = None
    effects_manifest: EffectsManifest | None = None
    public_api_changed: bool = False
    verification: list[Verification] = Field(default_factory=list)
    evidence_uris: EvidenceUris = Field(default_factory=EvidenceUris)
    source_ref: str
    packet_sha256: str | None = None
    adapter_results: list[AdapterResult] = Field(default_factory=list)
    # Landed≠live: manage sync_restart / plugin-install action lines (decision:closeout-propagation-residue).
    propagation_residue: list[str] = Field(default_factory=list)
    # Structured harvest rows — authoritative when non-empty over propagation_residue.
    propagation: list[PropagationRow] = Field(default_factory=list)
