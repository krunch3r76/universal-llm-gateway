"""Batch-path Share URI ingress for ``fs(op=read_multi)``.

Single-path ops resolve via ``resolve_fs_ingress`` when ``path`` is set.
``read_multi`` drives ``paths`` instead; without this helper those entries
reach cortex ``resolve_files_path`` still carrying ``cortex://`` and look
absent (friction a:37216 / a:29343).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from implement_admission.scheme_resolve import resolve_fs_ingress
from tool_error_enricher import apply_life_sandbox_default


@dataclass(frozen=True)
class BatchIngressResult:
    sandbox: str
    resolved_paths: list[str]
    original_paths: list[str]
    meta: dict[str, Any]


@dataclass
class CallIngress:
    """Resolved sandbox + path(s) ready for ``fs_impl`` dispatch."""

    sandbox: str
    path: str
    paths: list[str] | None
    batch_originals: list[str] | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _entry_sandbox_hint(
    raw: str,
    *,
    surface: str,
    sandbox: str | None,
) -> str | None:
    """Per-path sandbox for batch ingress when the caller left sandbox blank.

    Life's empty-``path`` default must not force ``cortex`` onto a schemed
    ``workspaces://`` entry (review B1 on a:37216). Schemed entries keep
    ``None`` so ``resolve_fs_ingress`` infers from the URI; schemeless life
    entries still default to cortex.
    """
    if sandbox:
        return sandbox
    from implement_admission.scheme_resolve import parse_schemed_path

    if parse_schemed_path(raw).scheme is not None:
        return None
    if surface == "life":
        return "cortex"
    return None


def resolve_paths_batch_ingress(
    paths: list[str],
    *,
    sandbox: str | None,
    surface: str = "code",
    for_write: bool = False,
    cortex_root: Path | None = None,
) -> BatchIngressResult:
    """Resolve each batch path the same way single ``path`` ingress does.

    All entries must land in one sandbox (``fs`` dispatches one sandbox per
    call). Mixed ``cortex://`` + ``workspaces://`` batches raise ``ValueError``.
    """
    if not paths:
        raise ValueError("'paths' is required for read_multi")

    root = cortex_root if cortex_root is not None else cortex_files_root()
    resolved: list[str] = []
    originals: list[str] = []
    sandboxes: list[str] = []
    meta: dict[str, Any] = {}

    for raw in paths:
        entry_sandbox = _entry_sandbox_hint(raw, surface=surface, sandbox=sandbox)
        ingress = resolve_fs_ingress(
            raw,
            sandbox=entry_sandbox,
            cortex_root=root,
            for_write=for_write,
        )
        resolved.append(ingress.rel_path)
        originals.append(raw)
        sandboxes.append(ingress.sandbox)
        if ingress.path_input_normalized:
            meta["path_input_normalized"] = True
        if ingress.normalization_advisory:
            meta["normalization_advisory"] = ingress.normalization_advisory

    unique = set(sandboxes)
    if len(unique) != 1:
        raise ValueError(
            "read_multi paths must resolve to one sandbox; "
            f"got mixed {sorted(unique)} from {paths!r}"
        )
    return BatchIngressResult(
        sandbox=next(iter(unique)),
        resolved_paths=resolved,
        original_paths=originals,
        meta=meta,
    )


def prepare_fs_call_ingress(
    *,
    surface: str,
    sandbox: str,
    path: str,
    paths: list[str] | None,
    for_write: bool,
    resolve_batch: bool = False,
) -> CallIngress:
    """Life default + single-path + optional batch-path Share URI resolution.

    ``resolve_batch`` is True only for ``op=read_multi``. When ``path`` is blank
    and ``paths`` is set, the life empty-path cortex default is *not* applied to
    the whole call — each entry gets a scheme-aware hint instead (B1).
    """
    meta: dict[str, Any] = {}
    effective_paths = list(paths) if paths else None
    batch_only = bool(resolve_batch and effective_paths and not path.strip())
    if batch_only:
        # Keep explicit sandbox only — do not force cortex from empty path=.
        effective_sandbox = sandbox.strip()
    else:
        effective_sandbox = apply_life_sandbox_default(
            surface=surface,
            sandbox=sandbox,
            path=path,
        )
    effective_path = path
    batch_originals: list[str] | None = None
    root = cortex_files_root()

    if path.strip():
        try:
            ingress = resolve_fs_ingress(
                path,
                sandbox=effective_sandbox or None,
                cortex_root=root,
                for_write=for_write,
            )
        except ValueError as exc:
            return CallIngress(
                sandbox=effective_sandbox or "",
                path=path,
                paths=effective_paths,
                error=str(exc),
            )
        effective_sandbox = ingress.sandbox
        effective_path = ingress.rel_path
        if ingress.path_input_normalized:
            meta["path_input_normalized"] = True
        if ingress.normalization_advisory:
            meta["normalization_advisory"] = ingress.normalization_advisory

    # read_multi drives paths=, not path= — resolve Share URIs before dispatch
    # so cortex:// does not reach resolve_files_path as a literal (a:37216).
    if resolve_batch and effective_paths:
        try:
            batch = resolve_paths_batch_ingress(
                effective_paths,
                sandbox=effective_sandbox or None,
                surface=surface,
                for_write=False,
                cortex_root=root,
            )
        except ValueError as exc:
            return CallIngress(
                sandbox=effective_sandbox or "",
                path=effective_path,
                paths=effective_paths,
                error=str(exc),
            )
        effective_sandbox = batch.sandbox
        effective_paths = batch.resolved_paths
        batch_originals = batch.original_paths
        meta.update(batch.meta)

    return CallIngress(
        sandbox=effective_sandbox or "",
        path=effective_path,
        paths=effective_paths,
        batch_originals=batch_originals,
        meta=meta,
    )


def remap_batch_files_keys(
    files: dict[str, Any],
    *,
    original_paths: list[str],
    resolved_paths: list[str],
) -> dict[str, Any]:
    """Restore caller-facing keys after ingress rewrote paths to rel form."""
    remapped: dict[str, Any] = {}
    for orig, rel in zip(original_paths, resolved_paths, strict=True):
        if rel in files:
            remapped[orig] = files[rel]
        elif orig in files:
            remapped[orig] = files[orig]
    for key, value in files.items():
        if key not in remapped and key not in resolved_paths:
            remapped[key] = value
    return remapped


def attach_batch_key_remap(
    result: dict[str, Any],
    *,
    batch_originals: list[str] | None,
    resolved_paths: list[str] | None,
) -> dict[str, Any]:
    """Remap ``files`` keys to caller URIs when batch ingress rewrote paths."""
    if (
        batch_originals is None
        or resolved_paths is None
        or "error" in result
        or not isinstance(result.get("files"), dict)
    ):
        return result
    result["files"] = remap_batch_files_keys(
        result["files"],
        original_paths=batch_originals,
        resolved_paths=resolved_paths,
    )
    return result


def apply_lane_ingress(
    *,
    path: str,
    sandbox: str,
    for_write: bool,
    lane_root: Path,
) -> tuple[str, dict[str, Any]]:
    """Re-resolve ingress against a lane-bound worktree root."""
    ingress = resolve_fs_ingress(
        path,
        sandbox=sandbox,
        workspaces_root_override=lane_root,
        cortex_root=cortex_files_root(),
        for_write=for_write,
    )
    meta: dict[str, Any] = {}
    if ingress.path_input_normalized:
        meta["path_input_normalized"] = True
    if ingress.normalization_advisory:
        meta["normalization_advisory"] = ingress.normalization_advisory
    return ingress.rel_path, meta
