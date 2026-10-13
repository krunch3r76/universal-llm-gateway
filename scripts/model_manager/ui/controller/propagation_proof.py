"""Propagation proof registry — live outside the charter_runner tick package."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx
from charter_runner_store.propagation_ledger import OpenPropagationProjection
from deploy_identity.code_ref_relation import code_ref_relation
from implement_admission.propagation_admit_validation import (
    CLIENT_VISIBLE_SERVICES,
    SERVED_ARTIFACT_SERVICES,
)
from implement_admission.propagation_row import PropagationRow, default_proof

ProbeCallable = Callable[[PropagationRow], dict[str, Any] | None]

_probe_client: httpx.Client | None = None


@dataclass(frozen=True)
class ProbeDispatchResult:
    """Outcome of dispatching one row's proof_class to a registered probe."""

    payload: dict[str, Any] | None
    proof_class_requested: str
    proof_class_executed: str | None
    error: str | None


def _fetch_json(url: str, *, timeout_s: float = 3.0) -> dict[str, Any] | None:
    global _probe_client
    try:
        client = _probe_client or httpx.Client(timeout=timeout_s)
        resp = client.get(url)
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except (httpx.HTTPError, ValueError, OSError):
        return None


def _probe_process_live_row(row: PropagationRow) -> dict[str, Any] | None:
    from services.git_integration_worker.relay.propagation_probe import (
        probe_process_live,
    )

    payload = probe_process_live(row.service)
    if not isinstance(payload, dict):
        return payload
    version = payload.get("code_version")
    if isinstance(version, str):
        return {
            **payload,
            "proof_class_executed": "process_live",
            "code_ref_relation": code_ref_relation(row.code_ref, version),
        }
    return {**payload, "proof_class_executed": "process_live"}


def _probe_client_visible_row(row: PropagationRow) -> dict[str, Any] | None:
    from deploy_identity.mcp_health_probe_url import resolve_mcp_health_probe_url
    from implement_admission.propagation_close_surfaces import resolve_close_surfaces

    from services.git_integration_worker.relay.propagation_probe import (
        _fetch_cortex_api_health,
    )

    owed = resolve_close_surfaces(
        service=row.service,
        proof_class=row.proof_class,
        close_surfaces=row.close_surfaces,
        proof_payload=None,
    )
    mcp_health = (
        _fetch_json(resolve_mcp_health_probe_url()) if "mcp_health" in owed else None
    )
    cortex_health = _fetch_cortex_api_health() if "cortex_api" in owed else None
    if mcp_health is None and cortex_health is None:
        return None
    payload: dict[str, Any] = {
        "proof_class_executed": "client_visible",
    }
    if mcp_health is not None:
        payload["mcp_health"] = mcp_health
    if cortex_health is not None:
        payload["cortex_api"] = cortex_health
    for section in (mcp_health, cortex_health):
        if isinstance(section, dict):
            version = section.get("code_version")
            if isinstance(version, str):
                payload["code_ref_relation"] = code_ref_relation(row.code_ref, version)
    return payload


def _probe_served_artifact_row(row: PropagationRow) -> dict[str, Any] | None:
    from services.git_integration_worker.relay.propagation_served_artifact import (
        probe_served_artifact,
        served_artifact_descriptor,
    )

    descriptor = served_artifact_descriptor(row.service)
    if descriptor is None:
        return None
    expected = row.expected_x_mcp_count or descriptor.expected_x_mcp_count
    payload = probe_served_artifact(
        row.service,
        code_ref=row.code_ref,
        expected_x_mcp_count=expected,
    )
    if isinstance(payload, dict):
        return {**payload, "proof_class_executed": "served_artifact"}
    return payload


def _build_proof_probe_registry() -> dict[tuple[str, str], ProbeCallable]:
    from services.git_integration_worker.relay.propagation_probe import (
        process_live_probeable_services,
    )

    registry: dict[tuple[str, str], ProbeCallable] = {}
    for slug in process_live_probeable_services():
        registry[(slug, "process_live")] = _probe_process_live_row
    for slug in SERVED_ARTIFACT_SERVICES:
        registry[(slug, "served_artifact")] = _probe_served_artifact_row
    for slug in CLIENT_VISIBLE_SERVICES:
        registry[(slug, "client_visible")] = _probe_client_visible_row
    return registry


PROOF_PROBE_REGISTRY: dict[tuple[str, str], ProbeCallable] = (
    _build_proof_probe_registry()
)


def registered_proof_classes(service: str) -> frozenset[str]:
    slug = service.strip().lower()
    return frozenset(
        proof_class for (svc, proof_class) in PROOF_PROBE_REGISTRY if svc == slug
    )


def proof_class_unsupported_detail(service: str, proof_class: str) -> str:
    slug = service.strip().lower()
    registered = sorted(registered_proof_classes(slug))
    return (
        f"proof_class_unsupported: service={slug} "
        f"requested={proof_class.strip()} "
        f"registered={','.join(registered)}"
    )


def projection_to_row(row: OpenPropagationProjection) -> PropagationRow:
    from charter_runner_store.propagation_ledger import get_open_proof_payload
    from implement_admission.propagation_close_surfaces import resolve_close_surfaces

    proof = row.proof or default_proof(row.service, row.proof_class)
    proof_payload = get_open_proof_payload(row.row_id)
    close_surfaces = resolve_close_surfaces(
        service=row.service,
        proof_class=row.proof_class,
        close_surfaces=None,
        proof_payload=proof_payload,
    )
    return PropagationRow(
        service=row.service,
        code_ref=row.code_ref,
        safe_window=row.safe_window,
        proof=proof,
        proof_class=row.proof_class,  # type: ignore[arg-type]
        proof_class_requested=(row.proof_class_requested or row.proof_class),  # type: ignore[arg-type]
        allow_self_preempt=row.allow_self_preempt,
        force=row.force,
        revert_on_fail=row.revert_on_fail,
        close_surfaces=tuple(sorted(close_surfaces)),
    )


def dispatch_proof_probe(row: PropagationRow) -> ProbeDispatchResult:
    requested = row.proof_class_requested or row.proof_class
    probe_fn = PROOF_PROBE_REGISTRY.get((row.service, requested))
    if probe_fn is None:
        return ProbeDispatchResult(
            payload=None,
            proof_class_requested=requested,
            proof_class_executed=None,
            error=proof_class_unsupported_detail(row.service, requested),
        )
    payload = probe_fn(row)
    executed = requested
    if isinstance(payload, dict):
        executed = str(payload.get("proof_class_executed") or requested)
    return ProbeDispatchResult(
        payload=payload,
        proof_class_requested=requested,
        proof_class_executed=executed,
        error=None,
    )


def dispatch_for_projection(row: OpenPropagationProjection) -> ProbeDispatchResult:
    return dispatch_proof_probe(projection_to_row(row))


def set_probe_client_for_tests(client: httpx.Client | None) -> None:
    global _probe_client
    _probe_client = client


__all__ = [
    "PROOF_PROBE_REGISTRY",
    "ProbeDispatchResult",
    "dispatch_for_projection",
    "dispatch_proof_probe",
    "projection_to_row",
    "proof_class_unsupported_detail",
    "registered_proof_classes",
    "set_probe_client_for_tests",
]
