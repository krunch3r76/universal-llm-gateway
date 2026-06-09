"""ImplementCloseout v1 envelope and source-specific adapter registry."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from implement_admission.closeout_helpers import source_from_ref
from implement_admission.closeout_models import (
    AdapterResult,
    EvidenceUris,
    ImplementCloseout,
    Verification,
)
from implement_admission.spec import CloseoutAdapterKind, CloseoutStatus, Source

__all__ = [
    "ADAPTERS",
    "AdapterResult",
    "CloseoutAdapter",
    "EvidenceUris",
    "ImplementCloseout",
    "Verification",
    "apply_closeout",
    "flatten_evidence_uris",
    "reconcile_closeout",
    "run_adapters",
    "run_composition",
]


class CloseoutAdapter(Protocol):
    def apply(
        self, closeout: ImplementCloseout, *, source: Source
    ) -> list[AdapterResult]: ...


def _load_adapters() -> dict[str, CloseoutAdapter]:
    from implement_admission.closeout_adapters import ADAPTER_INSTANCES

    return dict(ADAPTER_INSTANCES)


ADAPTERS: dict[str, CloseoutAdapter] = {}


def _adapters() -> dict[str, CloseoutAdapter]:
    global ADAPTERS
    if not ADAPTERS:
        ADAPTERS = _load_adapters()
    return ADAPTERS


def flatten_evidence_uris(evidence: EvidenceUris) -> list[str]:
    out: list[str] = []
    for thread in evidence.bus_threads:
        out.append(
            f"agent-bus:{thread}" if not thread.startswith("agent-bus:") else thread
        )
    out.extend(evidence.cortex_assertions)
    out.extend(evidence.artifact_paths)
    out.extend(evidence.git_refs)
    for dispatch_id in evidence.dispatch_ids:
        out.append(
            dispatch_id
            if dispatch_id.startswith("execution:")
            else f"execution:{dispatch_id}"
        )
    return out


def aggregate_adapter_status(statuses: list[str]) -> str:
    if not statuses:
        return CloseoutStatus.FAILED.value
    if all(s == CloseoutStatus.COMPLETE.value for s in statuses):
        return CloseoutStatus.COMPLETE.value
    if all(s == CloseoutStatus.FAILED.value for s in statuses):
        return CloseoutStatus.FAILED.value
    return CloseoutStatus.PARTIAL.value


def reconcile_closeout(
    closeout: ImplementCloseout, results: list[AdapterResult]
) -> ImplementCloseout:
    status_value = aggregate_adapter_status([r.status for r in results])
    return closeout.model_copy(
        update={
            "adapter_results": results,
            "status": CloseoutStatus(status_value),
        }
    )


def run_composition(
    closeout: ImplementCloseout, sources: list[Source]
) -> list[AdapterResult]:
    """MIXED aggregator over distinct resolved sub-sources."""
    seen: set[str] = set()
    ordered: list[Source] = []
    for src in sources:
        key = src.canonical_ref
        if key in seen:
            continue
        seen.add(key)
        ordered.append(src)

    results: list[AdapterResult] = []
    for src in ordered:
        kind = src.source_kind.value
        adapter = _adapters().get(kind)
        if adapter is None:
            results.append(
                AdapterResult(
                    adapter=CloseoutAdapterKind.MIXED.value,
                    status=CloseoutStatus.FAILED.value,
                    error=f"no adapter for {kind}",
                )
            )
            continue
        try:
            results.extend(adapter.apply(closeout, source=src))
        except Exception as exc:
            results.append(
                AdapterResult(
                    adapter=CloseoutAdapterKind.MIXED.value,
                    status=CloseoutStatus.FAILED.value,
                    error=str(exc),
                )
            )
    return results


def run_adapters(closeout: ImplementCloseout, source: Source) -> list[AdapterResult]:
    kind = (
        source.source_kind.value
        if isinstance(source.source_kind, StrEnum)
        else str(source.source_kind)
    )
    adapter = _adapters().get(kind)
    if adapter is None:
        return [
            AdapterResult(
                adapter=kind,
                status=CloseoutStatus.FAILED.value,
                error=f"no closeout adapter registered for source_kind={kind!r}",
            )
        ]
    return adapter.apply(closeout, source=source)


def apply_closeout(closeout: ImplementCloseout) -> ImplementCloseout:
    source = source_from_ref(closeout.source_ref)
    results = run_adapters(closeout, source)
    return reconcile_closeout(closeout, results)
