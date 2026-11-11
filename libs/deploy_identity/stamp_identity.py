"""Deploy-gate identity fields read from a source-sync stamp file."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

from deploy_identity.code_version import is_valid_sha40, read_stamp_sha
from deploy_identity.mcp_restart_gate import CONTAINER_NAME

_DEFAULT_STAMP_PATH = "/app/.source_sync_stamp"


def deploy_identity_from_stamp_path(path: Path) -> dict[str, str]:
    """Return gate-relevant identity fields parsed from a stamp on disk."""
    try:
        if not path.is_file():
            return {}
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    return deploy_identity_from_stamp_text(text)


def deploy_identity_from_stamp_text(text: str) -> dict[str, str]:
    """Parse stamp body into fields ``deploy_state_gate._identity_passes`` consumes."""
    lines = text.strip().splitlines()
    if not lines:
        return {}
    synced_at = lines[0].strip()
    if not synced_at:
        return {}
    metadata: dict[str, str] = {}
    for raw in lines[2:]:
        key, separator, value = raw.partition("=")
        if separator and key.strip() and value.strip():
            metadata[key.strip()] = value.strip()

    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False) as tmp:
        tmp.write(text)
        stamp_path = Path(tmp.name)
    try:
        sha = read_stamp_sha(stamp_path)
    finally:
        stamp_path.unlink(missing_ok=True)

    out: dict[str, str] = {
        "deploy_mode": "source_synced",
        "source_synced_at": synced_at,
    }
    if sha and is_valid_sha40(sha):
        out["source_ref"] = f"git:{sha}"
    tree = metadata.get("source_tree_sha", "").strip()
    if tree and is_valid_sha40(tree):
        out["source_tree_hash"] = tree
    return out


def read_mcp_container_deploy_identity(
    container: str = CONTAINER_NAME,
    *,
    stamp_path: str = _DEFAULT_STAMP_PATH,
) -> tuple[dict[str, Any], str | None]:
    """Read MCP container stamp via docker exec; never invent field values."""
    try:
        proc = subprocess.run(
            ["docker", "exec", container, "cat", stamp_path],
            capture_output=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {}, f"stamp_exec:{type(exc).__name__}"
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", errors="replace").strip()
        suffix = f":{detail}" if detail else ""
        return {}, f"stamp_exec:exit_{proc.returncode}{suffix}"
    text = proc.stdout.decode("utf-8", errors="replace")
    fields = deploy_identity_from_stamp_text(text)
    if not fields:
        return {}, "stamp_unreadable"
    return fields, None


__all__ = [
    "deploy_identity_from_stamp_path",
    "deploy_identity_from_stamp_text",
    "read_mcp_container_deploy_identity",
]
