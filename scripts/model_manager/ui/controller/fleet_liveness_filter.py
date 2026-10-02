"""Service-subset selection for fleet_liveness snapshots."""

from __future__ import annotations

from .fleet_liveness_probe import SERVICE_SLUGS


def normalize_service_filter(
    service: str | None = None,
    services: list[str] | None = None,
) -> tuple[str, ...] | None:
    """Return an ordered ``SERVICE_SLUGS`` subset, or ``None`` for the full fleet.

    Absent ``service`` and ``services`` preserves today's unfiltered snapshot.
    Provided empty ``services`` (with no ``service``) and unknown slugs raise —
    omit is not the same as an empty or silent miss (omit ≠ healthy).
    """
    if service is None and services is None:
        return None

    requested: list[str] = []
    if service is not None:
        if not isinstance(service, str) or not service.strip():
            raise ValueError("fleet_liveness service must be a non-empty string")
        requested.append(service.strip())
    if services is not None:
        if not isinstance(services, list):
            raise ValueError("fleet_liveness services must be a list of strings")
        if not services and service is None:
            raise ValueError(
                "fleet_liveness services must be non-empty when provided"
            )
        for item in services:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "fleet_liveness services entries must be non-empty strings"
                )
            requested.append(item.strip())
    if not requested:
        raise ValueError("fleet_liveness services must be non-empty when provided")

    known = set(SERVICE_SLUGS)
    unknown = sorted({slug for slug in requested if slug not in known})
    if unknown:
        raise ValueError(
            "fleet_liveness unknown service(s): "
            + ", ".join(unknown)
            + "; known: "
            + ", ".join(SERVICE_SLUGS)
        )
    wanted = set(requested)
    return tuple(slug for slug in SERVICE_SLUGS if slug in wanted)


__all__ = ["normalize_service_filter"]
