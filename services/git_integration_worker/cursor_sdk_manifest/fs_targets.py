"""FS-surface target parse, absolute resolve, and write-target extraction.

Parses ``sandbox:rel`` / share-URI manifest targets, resolves them against
mount/cortex roots, and lists write-family ``(sandbox, path)`` pairs. Invariant:
``parse_fs_manifest_target`` keeps ``resolve_fs_ingress`` function-local (arch
fs_targets map; ``scheme_resolve`` has no reverse import — still do not hoist).
``_FS_WRITE_OPS`` membership is the write-family test; do not infer writes from
op name alone. Depends only on ``surface_taxonomy`` for that frozenset.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from implement_admission.closeout_models import EffectEntry, EffectsManifest

from . import surface_taxonomy


def parse_fs_manifest_target(target: str | None) -> tuple[str, str] | None:
    if not target:
        return None
    # Function-local per arch fs_targets; scheme_resolve has no reverse import. Do not hoist (split-fidelity).
    from implement_admission.scheme_resolve import resolve_fs_ingress

    try:
        ingress = resolve_fs_ingress(target)
        return ingress.sandbox, ingress.rel_path.lstrip("/")
    except ValueError:
        pass
    if ":" not in target:
        return None
    sandbox, rel = target.split(":", 1)
    sandbox = sandbox.strip()
    rel = rel.strip().lstrip("/")
    if sandbox and rel:
        return sandbox, rel
    return None


def resolve_fs_target_absolute(
    target: str | None,
    *,
    mount_root: Path,
    cortex_root: Path,
) -> Path | None:
    if not target:
        return None
    parsed = parse_fs_manifest_target(target)
    if parsed is not None:
        sandbox, rel = parsed
        if sandbox == "workspaces":
            return (mount_root / rel).resolve()
        if sandbox == "cortex":
            return (cortex_root / rel).resolve()
        return (mount_root / rel).resolve()
    return (mount_root / target.lstrip("/")).resolve()
def manifest_fs_targets(manifest: EffectsManifest | None) -> list[str]:
    if manifest is None:
        return []
    section = manifest.surfaces.get("fs")
    if section is None:
        return []
    targets: list[str] = []
    for entry in section.entries:
        target = entry.target or entry.identity
        if target:
            targets.append(target)
    return targets


def _fs_entry_write_op(entry: EffectEntry) -> str | None:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if detail:
        op = detail.get("op")
        if isinstance(op, str) and op in surface_taxonomy._FS_WRITE_OPS:
            return op
    return None


def _fs_entry_path(entry: EffectEntry) -> str | None:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if detail:
        path = detail.get("path")
        if isinstance(path, str) and path.strip():
            return path.strip()
    target = entry.target or entry.identity
    return target.strip() if isinstance(target, str) and target.strip() else None


def _fs_entry_sandbox(entry: EffectEntry) -> str | None:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if not detail:
        return None
    sandbox = detail.get("sandbox")
    return sandbox.strip() if isinstance(sandbox, str) and sandbox.strip() else None


def manifest_fs_write_targets(
    manifest: EffectsManifest | None,
) -> list[tuple[str | None, str]]:
    if manifest is None:
        return []
    section = manifest.surfaces.get("fs")
    if section is None:
        return []
    targets: list[tuple[str | None, str]] = []
    for entry in section.entries:
        if _fs_entry_write_op(entry) is None:
            continue
        path = _fs_entry_path(entry)
        if not path:
            continue
        targets.append((_fs_entry_sandbox(entry), path))
    return targets
