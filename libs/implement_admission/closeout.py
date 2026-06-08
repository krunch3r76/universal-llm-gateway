"""ImplementCloseout v1 envelope and source-specific adapter registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field

from implement_admission.spec import CloseoutAdapterKind, CloseoutStatus, Source


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


class CloseoutAdapter(Protocol):
    def apply(self, closeout: ImplementCloseout, *, source: Source) -> AdapterResult: ...


class _StubAdapter:
    def __init__(self, kind: str) -> None:
        self._kind = kind

    def apply(self, closeout: ImplementCloseout, *, source: Source) -> AdapterResult:
        raise NotImplementedError("phase 4")


def _stub(kind: str) -> CloseoutAdapter:
    return _StubAdapter(kind)


ADAPTERS: dict[str, CloseoutAdapter] = {
    CloseoutAdapterKind.TODO: _stub("todo"),
    CloseoutAdapterKind.PLAN: _stub("plan"),
    CloseoutAdapterKind.PLAN_PHASE: _stub("plan_phase"),
    CloseoutAdapterKind.PACKET: _stub("packet"),
    CloseoutAdapterKind.AGENT_BUS: _stub("agent-bus"),
    CloseoutAdapterKind.MIXED: _stub("mixed"),
}


def run_adapters(closeout: ImplementCloseout, source: Source) -> list[AdapterResult]:
    """Select adapter(s) by source kind and apply (stubs raise at P0/P1)."""
    kind = source.source_kind.value if isinstance(source.source_kind, StrEnum) else str(source.source_kind)
    if kind == CloseoutAdapterKind.MIXED:
        keys = [
            CloseoutAdapterKind.TODO,
            CloseoutAdapterKind.PLAN,
            CloseoutAdapterKind.PLAN_PHASE,
            CloseoutAdapterKind.PACKET,
            CloseoutAdapterKind.AGENT_BUS,
        ]
        return [ADAPTERS[k.value].apply(closeout, source=source) for k in keys]
    adapter = ADAPTERS.get(kind)
    if adapter is None:
        msg = f"no closeout adapter registered for source_kind={kind!r}"
        raise KeyError(msg)
    return [adapter.apply(closeout, source=source)]
