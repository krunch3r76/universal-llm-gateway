"""Admission-time validation for structured propagation rows."""

from __future__ import annotations

MANAGE_SERVICE_SLUGS = frozenset(
    {
        "agent_bus",
        "cdp_ask",
        "cloud_proxy",
        "cortex_api",
        "email_bridge",
        "event_service",
        "gateway",
        "git_integration_worker",
        "mcp",
        "rag",
        "stargate",
    }
)

SAFE_WINDOW_VALUES = frozenset({"harvest", "standalone_ok", "drain_required"})

LEGAL_SAFE_WINDOW_LIST = "harvest, standalone_ok, drain_required"

PROOF_CLASS_VALUES = frozenset({"process_live", "client_visible", "served_artifact"})

# Services with client-reachable served OpenAPI surfaces (see propagation_served_artifact).
SERVED_ARTIFACT_SERVICES = frozenset(
    {"agent_bus", "cortex_api", "git_integration_worker", "rag"}
)

# Services whose default closure path is composite health, not served OpenAPI.
CLIENT_VISIBLE_SERVICES = frozenset({"mcp"})


def validate_service_slug(service: str) -> str | None:
    """Return an error token when *service* is not a known manage slug."""
    slug = service.strip().lower()
    if slug not in MANAGE_SERVICE_SLUGS:
        return f"unknown_service:{slug}; valid: {', '.join(sorted(MANAGE_SERVICE_SLUGS))}"
    return None


def validate_safe_window(raw: object) -> str | None:
    """Return an error token when an explicit ``safe_window`` is illegal."""
    if raw is None:
        return None
    if not isinstance(raw, str) or raw.strip() not in SAFE_WINDOW_VALUES:
        value = raw if isinstance(raw, str) else type(raw).__name__
        return f"invalid_safe_window:{value}; legal: {LEGAL_SAFE_WINDOW_LIST}"
    return None


def legal_proof_classes(service: str) -> frozenset[str]:
    """Return proof classes a service can satisfy at probe time.

    ``process_live`` is legal only when the probe module exposes a fetcher for
    the slug (same oracle as ``PROOF_PROBE_REGISTRY``) — never advertise a class
    the registry refuses to register.
    """
    slug = service.strip().lower()
    legal: set[str] = set()
    # Lazy import: avoid libs→services cycle at module load; oracle SoT is the
    # fetcher map in propagation_probe (6907 item-2 adds unlock advertisement).
    from services.git_integration_worker.cursor_auto.propagation_probe import (
        process_live_probeable_services,
    )

    if slug in process_live_probeable_services():
        legal.add("process_live")
    if slug in SERVED_ARTIFACT_SERVICES:
        legal.add("served_artifact")
    if slug in CLIENT_VISIBLE_SERVICES:
        legal.add("client_visible")
    return frozenset(legal)


def validate_proof_class(service: str, proof_class: str) -> str | None:
    """Return an error token when ``proof_class`` is unsupported for *service*."""
    slug = service.strip().lower()
    pc = proof_class.strip()
    if pc not in PROOF_CLASS_VALUES:
        return f"unknown_proof_class:{pc}"
    legal = legal_proof_classes(slug)
    if pc not in legal:
        return (
            f"invalid_proof_class:{pc}; "
            f"legal for {slug}: {', '.join(sorted(legal))}"
        )
    return None


__all__ = [
    "CLIENT_VISIBLE_SERVICES",
    "LEGAL_SAFE_WINDOW_LIST",
    "MANAGE_SERVICE_SLUGS",
    "PROOF_CLASS_VALUES",
    "SAFE_WINDOW_VALUES",
    "SERVED_ARTIFACT_SERVICES",
    "legal_proof_classes",
    "validate_proof_class",
    "validate_safe_window",
    "validate_service_slug",
]
