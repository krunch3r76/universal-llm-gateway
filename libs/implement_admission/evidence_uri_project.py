"""Read-time projection of stored evidence URIs to canonical Share URIs (Fork I)."""

from __future__ import annotations

from pathlib import Path

from implement_admission.closeout_helpers import cortex_files_root, workspaces_root
from implement_admission.scheme_resolve import parse_schemed_path
from implement_admission.share_uri_emit import to_share_uri


def project_evidence_uri_for_display(stored_uri: str) -> str:
    """Map legacy absolute/files:///bare paths to canonical Share URI at read time.

    DB rows are never mutated — this is egress projection only.
    """
    raw = stored_uri.strip()
    if not raw:
        return raw
    if raw.startswith(("https://", "http://", "agent-bus:", "transcript:")):
        return raw

    parsed = parse_schemed_path(raw)
    if parsed.scheme in ("workspaces", "ws"):
        return to_share_uri("workspaces", parsed.rel_path)
    if parsed.scheme == "cortex":
        return to_share_uri("cortex", parsed.rel_path)

    if raw.startswith("files://"):
        body = raw[len("files://") :]
        p = Path(body)
        if not p.is_absolute():
            p = cortex_files_root() / body.lstrip("/")
        try:
            rel = p.resolve().relative_to(cortex_files_root().resolve()).as_posix()
            return to_share_uri("cortex", rel)
        except ValueError:
            return raw

    if raw.startswith("/"):
        p = Path(raw).resolve()
        roots = [
            cortex_files_root(),
            Path("/mnt/torus/mcp-data/files"),
            Path("/data/files"),
        ]
        for root in roots:
            try:
                rel = p.relative_to(root.resolve()).as_posix()
                return to_share_uri("cortex", rel)
            except ValueError:
                continue
        try:
            rel = p.relative_to(workspaces_root().resolve()).as_posix()
            return to_share_uri("workspaces", rel)
        except ValueError:
            return raw

    return to_share_uri("cortex", raw.lstrip("/"))


__all__ = ["project_evidence_uri_for_display"]
