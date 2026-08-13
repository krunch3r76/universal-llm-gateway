"""Mint-time close-surface composition from verified consumer import reach."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from implement_admission.consumer_import_verify import verify_consumer_import

ProofClass = Literal["process_live", "client_visible", "served_artifact"]

# Composite client_visible sections for mcp rows — maps surface → consumer slug probe.
_CLIENT_VISIBLE_MCP_SURFACE_CONSUMER: dict[str, str | None] = {
    "mcp_health": None,  # row service itself — always owed when service=mcp
    "cortex_api": "cortex_api",
}


@dataclass(frozen=True)
class ExcludedSurface:
    """One close surface omitted because the commit cannot affect it."""

    surface: str
    import_path: str
    evidence_paths: tuple[str, ...]


@dataclass(frozen=True)
class CloseSurfaceComposition:
    """Mint-bound close surfaces plus recorded exclusions."""

    close_surfaces: tuple[str, ...]
    excluded_surfaces: tuple[ExcludedSurface, ...]


def default_close_surfaces(service: str, proof_class: str) -> frozenset[str]:
    """Return the full close-surface set for a proof class before import filtering."""
    slug = (service or "").strip().lower()
    pc = (proof_class or "").strip()
    if pc == "client_visible" and slug == "mcp":
        return frozenset(_CLIENT_VISIBLE_MCP_SURFACE_CONSUMER)
    if pc == "served_artifact":
        return frozenset({"surfaces"})
    return frozenset({"code_version"})


def _cortex_api_reachable(paths: list[str]) -> tuple[bool, str, tuple[str, ...]]:
    """True when any *paths* can affect cortex_api (service path or verified import)."""
    evidence: list[str] = []
    for path in paths:
        if path.startswith("services/cortex_api/"):
            return True, "service_path", (path,)
        if not path.startswith("libs/"):
            continue
        status = verify_consumer_import("cortex_api", path)
        evidence.append(path)
        if status == "verified":
            return True, status, tuple(evidence)
    if not evidence:
        return False, "not_probed", ()
    # Summarize worst observed status for exclusion record.
    statuses = [verify_consumer_import("cortex_api", p) for p in evidence]
    if any(s == "contradicted" for s in statuses):
        return False, "contradicted", tuple(evidence)
    if any(s == "unverified" for s in statuses):
        return False, "unverified", tuple(evidence)
    return False, statuses[0] if statuses else "not_probed", tuple(evidence)


def compose_close_surfaces(
    service: str,
    proof_class: str,
    land_paths: list[str],
) -> CloseSurfaceComposition:
    """Derive owed close surfaces from import reach on *land_paths*.

    Surfaces the commit cannot affect are excluded and returned with probe
    evidence — never silently dropped.
    """
    slug = (service or "").strip().lower()
    pc = (proof_class or "").strip()
    defaults = default_close_surfaces(slug, pc)
    if pc == "client_visible" and slug == "mcp":
        included: list[str] = ["mcp_health"]
        excluded: list[ExcludedSurface] = []
        if "cortex_api" in defaults:
            reachable, import_path, evidence_paths = _cortex_api_reachable(land_paths)
            if reachable:
                included.append("cortex_api")
            else:
                excluded.append(
                    ExcludedSurface(
                        surface="cortex_api",
                        import_path=import_path,
                        evidence_paths=evidence_paths,
                    )
                )
        return CloseSurfaceComposition(
            close_surfaces=tuple(included),
            excluded_surfaces=tuple(excluded),
        )
    return CloseSurfaceComposition(
        close_surfaces=tuple(sorted(defaults)),
        excluded_surfaces=(),
    )


def compose_proof_for_surfaces(
    service: str,
    proof_class: str,
    close_surfaces: tuple[str, ...],
    *,
    expected_x_mcp_count: int | None = None,
) -> str:
    """Compose mint-time proof obligation prose for *close_surfaces* only."""
    from implement_admission.propagation_row import (
        MissingProofTemplateError,
        compose_proof,
    )

    slug = (service or "").strip().lower()
    pc = (proof_class or "").strip()
    if pc == "client_visible" and slug == "mcp":
        if close_surfaces == ("mcp_health",):
            return (
                "client_visible: GET /health → "
                "AFTER restart VERIFY mcp /health satisfies the code_ref ancestry check"
            )
        if set(close_surfaces) == {"mcp_health", "cortex_api"}:
            return compose_proof(slug, pc, expected_x_mcp_count=expected_x_mcp_count)
        raise MissingProofTemplateError(
            f"no proof template for (service={slug!r}, proof_class={pc!r}, "
            f"close_surfaces={close_surfaces!r})"
        )
    return compose_proof(slug, pc, expected_x_mcp_count=expected_x_mcp_count)


def excluded_surfaces_to_payload(
    excluded: tuple[ExcludedSurface, ...],
) -> list[dict[str, Any]]:
    """Serialize exclusions for open-row ``proof_payload`` persistence."""
    return [
        {
            "surface": item.surface,
            "import_path": item.import_path,
            "evidence_paths": list(item.evidence_paths),
        }
        for item in excluded
    ]


def excluded_surfaces_from_payload(
    raw: list[Any] | None,
) -> tuple[ExcludedSurface, ...]:
    """Parse persisted exclusion records."""
    if not raw:
        return ()
    out: list[ExcludedSurface] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        surface = item.get("surface")
        import_path = item.get("import_path")
        paths = item.get("evidence_paths")
        if not isinstance(surface, str) or not isinstance(import_path, str):
            continue
        evidence = tuple(str(p) for p in paths) if isinstance(paths, list) else ()
        out.append(
            ExcludedSurface(
                surface=surface,
                import_path=import_path,
                evidence_paths=evidence,
            )
        )
    return tuple(out)


def resolve_close_surfaces(
    *,
    service: str,
    proof_class: str,
    close_surfaces: tuple[str, ...] | None,
    proof_payload: dict[str, Any] | None,
) -> frozenset[str]:
    """Effective close surfaces for settle — row field, then mint payload, then default."""
    if close_surfaces:
        return frozenset(close_surfaces)
    if isinstance(proof_payload, dict):
        stored = proof_payload.get("close_surfaces")
        if isinstance(stored, list) and stored:
            return frozenset(str(item) for item in stored)
    return default_close_surfaces(service, proof_class)


__all__ = [
    "CloseSurfaceComposition",
    "ExcludedSurface",
    "compose_close_surfaces",
    "compose_proof_for_surfaces",
    "default_close_surfaces",
    "excluded_surfaces_from_payload",
    "excluded_surfaces_to_payload",
    "resolve_close_surfaces",
]
