"""Off-git URI canonicalization and OOB cortex-write findings.

Normalizes fs write-capture into ``cortex://`` / ``workspaces://`` URIs,
filters sidecar/closeout-receipt exclusions, collects expected cortex
deliverables from pinned/light-bounded/files_expected, and emits
``capture:oob_cortex_write_unobserved`` deviations when a landed cortex file
was not observed as an fs write. Invariant: repo-relative paths are never
inferred as cortex URIs (OOB policy on ``normalize_expected_cortex_deliverable_uri``).
Depends on ``fs_targets.manifest_fs_write_targets`` only.
"""

from __future__ import annotations

from pathlib import Path

from implement_admission.closeout_models import EffectsManifest

from . import fs_targets

def _normalize_offgit_uri(sandbox: str | None, path: str) -> str:
    raw = path.strip()
    lower = raw.lower()
    if lower.startswith("cortex://"):
        return raw
    if lower.startswith("workspaces://"):
        return raw
    if lower.startswith("cortex:"):
        return f"cortex://{raw.split(':', 1)[1].lstrip('/')}"
    sandbox_key = (sandbox or "").strip().lower()
    if sandbox_key == "cortex":
        return f"cortex://{raw.lstrip('/')}"
    if sandbox_key == "workspaces":
        return f"workspaces://{raw.lstrip('/')}"
    if ":" in raw and not lower.startswith(("cortex", "workspaces")):
        prefix, _, rest = raw.partition(":")
        if prefix.lower() in {"cortex", "workspaces"} and rest:
            scheme = prefix.lower()
            return f"{scheme}://{rest.lstrip('/')}"
    return f"workspaces://{raw.lstrip('/')}"


def _is_excluded_offgit_uri(uri: str, *, sidecar_ref: str) -> bool:
    if uri == sidecar_ref:
        return True
    lower = uri.lower()
    if "tmp/reviews/closeouts/" in lower:
        return True
    return False


def normalize_expected_cortex_deliverable_uri(raw: str) -> str | None:
    """Return canonical ``cortex://`` URI when *raw* is cortex-shaped; else None.

    Repo-relative paths are never inferred as cortex URIs (OOB policy).
    """
    stripped = raw.strip()
    if not stripped:
        return None
    lower = stripped.lower()
    if lower.startswith("cortex://"):
        return f"cortex://{stripped[9:].lstrip('/')}"
    if lower.startswith("cortex:"):
        return f"cortex://{stripped.split(':', 1)[1].lstrip('/')}"
    return None


def collect_expected_cortex_deliverable_uris(
    *,
    light_bounded_expected_paths: tuple[str, ...] = (),
    files_expected: list[str] | None = None,
    cortex_artifact_paths: list[str] | None = None,
) -> list[str]:
    """Deduped cortex:// deliverables from pinned, light-bounded, and files_expected."""
    seen: set[str] = set()
    ordered: list[str] = []
    for raw in (
        *(cortex_artifact_paths or []),
        *light_bounded_expected_paths,
        *(files_expected or []),
    ):
        uri = normalize_expected_cortex_deliverable_uri(raw)
        if uri and uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return ordered


def oob_cortex_write_findings(
    *,
    expected_cortex_uris: list[str],
    offgit_uris: list[str],
    cortex_root: Path,
) -> tuple[list[str], str | None]:
    """OOB deviations when a landed cortex deliverable is absent from fs write-capture."""
    offgit_set = set(offgit_uris)
    deviations: list[str] = []
    for uri in expected_cortex_uris:
        if uri in offgit_set:
            continue
        rel = uri[9:].lstrip("/") if uri.lower().startswith("cortex://") else uri
        if not (cortex_root / rel).exists():
            continue
        deviations.append(f"capture:oob_cortex_write_unobserved:{uri}")
    divergence_reason = "capture:oob_cortex_write_unobserved" if deviations else None
    return deviations, divergence_reason


def manifest_offgit_deliverable_uris(
    manifest: EffectsManifest | None,
    *,
    sidecar_ref: str,
) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for sandbox, path in fs_targets.manifest_fs_write_targets(manifest):
        uri = _normalize_offgit_uri(sandbox, path)
        if _is_excluded_offgit_uri(uri, sidecar_ref=sidecar_ref):
            continue
        if uri not in seen:
            seen.add(uri)
            ordered.append(uri)
    return ordered
