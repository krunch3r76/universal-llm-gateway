"""Coerce ``expected_read_sha256`` at the unified fs boundary before markdown dispatch."""

from __future__ import annotations

from typing import Any

from tools._file_helpers import _path_match_keys
from tools._sparse_document_read import param_not_applicable_error

_MD_OPS_REJECT_MAP = frozenset({"md_to_dict"})


def _path_lookup_aliases(path: str, sandbox: str) -> set[str]:
    aliases = set(_path_match_keys(path))
    bare = str(path or "").strip().lstrip("/")
    if bare:
        aliases.add(bare)
    if bare and sandbox:
        aliases.add(f"{sandbox}://{bare}")
    return aliases


def resolve_expected_read_from_map(
    exp_map: dict[str, str],
    *,
    path: str,
    sandbox: str,
) -> tuple[str | None, list[str]]:
    """Return digest for *path* or the map keys when none match."""
    aliases = _path_lookup_aliases(path, sandbox)
    for key, value in exp_map.items():
        key_aliases = _path_match_keys(key)
        if aliases & key_aliases:
            return str(value), []
    return None, [str(k) for k in exp_map.keys()]


def coerce_expected_read_sha256_for_md(
    *,
    op: str,
    expected: str | dict[str, str],
    path: str,
    sandbox: str,
) -> tuple[str, dict[str, Any] | None]:
    """Normalize map-shaped preconditions to a scalar before markdown tools run."""
    if not isinstance(expected, dict):
        if expected is None:
            return "", None
        return str(expected), None
    if not expected:
        return "", param_not_applicable_error(
            parameter="expected_read_sha256",
            keys=[],
        )
    if op in _MD_OPS_REJECT_MAP:
        return "", param_not_applicable_error(
            parameter="expected_read_sha256",
            keys=list(expected.keys()),
        )
    digest, bad_keys = resolve_expected_read_from_map(
        expected, path=path, sandbox=sandbox
    )
    if digest is None:
        return "", param_not_applicable_error(
            parameter="expected_read_sha256",
            keys=bad_keys,
        )
    return digest, None
