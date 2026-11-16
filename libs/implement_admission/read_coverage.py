"""Read-coverage inventory — manifest read evidence vs packet ``required_reads``."""

from __future__ import annotations

from collections.abc import Mapping

from implement_admission.closeout_models import (
    EffectEntry,
    EffectsManifest,
    ReadInventoryRow,
)

_FS_READ_OPS = frozenset({"read", "md_read", "list", "glob", "grep", "search"})
_REPO_READ_OP = "observed"


def _normalize_path_token(path: str) -> str:
    text = path.strip().strip("`\"'")
    if text.startswith("./"):
        text = text[2:]
    return text


def _manifest_read_paths(manifest: EffectsManifest | None) -> set[str]:
    if manifest is None:
        return set()
    paths: set[str] = set()
    repo = manifest.surfaces.get("repo")
    if repo is not None:
        for entry in repo.entries:
            if entry.op == _REPO_READ_OP:
                _collect_entry_path(entry, paths)
    fs = manifest.surfaces.get("fs")
    if fs is not None:
        for entry in fs.entries:
            if _fs_entry_is_read(entry):
                _collect_entry_path(entry, paths)
    return paths


def _fs_entry_is_read(entry: EffectEntry) -> bool:
    detail = entry.detail if isinstance(entry.detail, Mapping) else None
    if detail:
        op = detail.get("op")
        if isinstance(op, str) and op in _FS_READ_OPS:
            return True
    return False


def _collect_entry_path(entry: EffectEntry, paths: set[str]) -> None:
    for raw in (entry.target, entry.identity):
        if isinstance(raw, str) and raw.strip():
            paths.add(_normalize_path_token(raw))


def _path_observed_in_manifest(required: str, manifest_paths: set[str]) -> bool:
    want = _normalize_path_token(required)
    if not want:
        return False
    if want in manifest_paths:
        return True
    want_lower = want.lower()
    for observed in manifest_paths:
        obs = _normalize_path_token(observed)
        if not obs:
            continue
        if obs == want:
            return True
        if obs.lower() == want_lower:
            return True
        if want.endswith(obs) or obs.endswith(want):
            if want.endswith(f"/{obs}") or obs.endswith(f"/{want}"):
                return True
            if "/" not in obs or "/" not in want:
                if obs.split("/")[-1] == want.split("/")[-1]:
                    return True
    if "://" in want:
        bare = want.split("://", 1)[-1]
        for observed in manifest_paths:
            if bare in observed or observed.endswith(bare):
                return True
    return False


def build_read_inventory(
    required_reads: list[str],
    manifest: EffectsManifest | None,
) -> list[ReadInventoryRow]:
    """One row per required path; ``observed`` only when manifest read evidence matches."""
    if not required_reads:
        return []
    manifest_paths = _manifest_read_paths(manifest)
    rows: list[ReadInventoryRow] = []
    for raw in required_reads:
        path = _normalize_path_token(raw)
        if not path:
            continue
        if _path_observed_in_manifest(path, manifest_paths):
            rows.append(
                ReadInventoryRow(path=path, observed=True, source="manifest")
            )
        else:
            rows.append(ReadInventoryRow(path=path, observed=False, source="seat"))
    return rows
