"""ImplementCloseout v1 pydantic models — no adapter imports."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from implement_admission.spec import CloseoutStatus


class Verification(BaseModel):
    command: str
    exit_code: int


class EvidenceUris(BaseModel):
    dispatch_ids: list[str] = Field(default_factory=list)
    bus_threads: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    cortex_assertions: list[str] = Field(default_factory=list)
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
    public_api_changed: bool = False
    verification: list[Verification] = Field(default_factory=list)
    evidence_uris: EvidenceUris = Field(default_factory=EvidenceUris)
    source_ref: str
    packet_sha256: str | None = None
    adapter_results: list[AdapterResult] = Field(default_factory=list)
