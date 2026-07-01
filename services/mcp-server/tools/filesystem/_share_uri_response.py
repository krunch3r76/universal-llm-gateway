"""Dual-carry Share URI helpers for fs MCP responses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from implement_admission.scheme_resolve import resolve_fs_ingress
from implement_admission.share_uri_emit import dual_carry, sandbox_rel, to_share_uri


def resolve_fs_path(
    path: str,
    sandbox: str = "",
    *,
    require_file: bool = False,
) -> tuple[str, str, Path | None, dict[str, Any]]:
    """Resolve fs path; return (sandbox, rel, resolved, meta extras)."""
    explicit = sandbox.strip() or None
    try:
        ingress = resolve_fs_ingress(path, sandbox=explicit)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc

    meta: dict[str, Any] = {}
    if ingress.path_input_normalized:
        meta["path_input_normalized"] = True
    if ingress.normalization_advisory:
        meta["normalization_advisory"] = ingress.normalization_advisory

    if require_file and ingress.resolved is None:
        raise FileNotFoundError(f"File not found: {path!r}")

    return ingress.sandbox, ingress.rel_path, ingress.resolved, meta


def attach_dual_carry(
    payload: dict[str, Any],
    *,
    sandbox: str,
    rel_path: str,
    abs_path: Path | None = None,
) -> dict[str, Any]:
    """Merge dual-carry path+uri into *payload*; strip absolute mount strings."""
    clean = rel_path.lstrip("/")
    out = dict(payload)
    out["path"] = clean
    out["uri"] = to_share_uri(sandbox, clean)
    for key in ("from", "to", "resolved", "source", "destination"):
        if key in out:
            val = out[key]
            if isinstance(val, str) and val.startswith("/"):
                try:
                    out[key] = sandbox_rel(sandbox, Path(val))
                except ValueError:
                    del out[key]
    if abs_path is not None:
        out.pop("resolved", None)
    return out


def dual_carry_result(
    sandbox: str,
    rel_path: str,
    **extra: Any,
) -> dict[str, Any]:
    return dual_carry(sandbox, rel_path, **extra)


__all__ = [
    "attach_dual_carry",
    "dual_carry_result",
    "resolve_fs_path",
]
