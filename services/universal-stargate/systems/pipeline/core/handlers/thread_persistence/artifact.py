"""Write per-turn JSON artifacts to the workspace ``.runtime/`` tree.

Each artifact is keyed by ``(chat_id, turn_index)`` and lives at
``workspaces://universal-llm-gateway/.runtime/thread-artifacts/{chat_id}/turn_{N:04d}.json``.
The on-disk path is computed from ``WORKSPACE_ROOT`` when set (Docker /
edge container deployment — ``docker/compose/gpu-edge.yml`` sets
``WORKSPACE_ROOT=/app``) and otherwise from this module's location —
matching the catalog-loading detection pattern in
``services/_universal-llm-gateway/src/core/catalog/loading.py``. The
universal-llm-gateway master Stargate runs as a host process and the
host path resolves correctly; an edge-container deployment requires a
``.runtime`` bind-mount which is not yet present in ``gpu-edge.yml``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

_ARTIFACT_SUBDIR = (".runtime", "thread-artifacts")
_WORKSPACES_SCHEME = "workspaces://universal-llm-gateway/"


def _workspace_root() -> Path:
    """Resolve the workspace root for artifact writes.

    Precedence: ``WORKSPACE_ROOT`` env var (Docker), else this module's
    parent chain. Module path —
    ``<root>/services/universal-stargate/systems/pipeline/core/handlers/thread_persistence/artifact.py``
    — places the repo root at ``parents[7]``.
    """
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[7]


def resolve_artifact_path(uri: str) -> Path | None:
    """Resolve a ``workspaces://universal-llm-gateway/`` URI to a local path.

    Returns None when *uri* does not match the expected scheme/namespace so
    callers can silently skip non-workspace URIs without branching on scheme.
    """
    if not uri.startswith(_WORKSPACES_SCHEME):
        return None
    relative = uri[len(_WORKSPACES_SCHEME) :]
    return _workspace_root() / relative


def turn_artifact_uri(chat_id: str, turn_index: int) -> str:
    """Return the canonical ``workspaces://`` URI for a turn artifact."""
    return (
        "workspaces://universal-llm-gateway/.runtime/thread-artifacts/"
        f"{chat_id}/turn_{turn_index:04d}.json"
    )


async def write_turn_artifact(
    *,
    chat_id: str,
    turn_index: int,
    payload: dict[str, Any],
) -> str:
    """Write ``payload`` to the per-turn artifact and return its URI.

    Atomic on success — writes to a sibling ``.tmp`` path then renames
    into place so a mid-write crash never leaves a half-written JSON
    file visible to readers. Idempotent on ``(chat_id, turn_index)``:
    re-invocation overwrites the existing file rather than failing or
    appending. The parent directory is created lazily.
    """
    import aiofiles

    dir_path = _workspace_root().joinpath(*_ARTIFACT_SUBDIR, chat_id)
    dir_path.mkdir(parents=True, exist_ok=True)

    file_path = dir_path / f"turn_{turn_index:04d}.json"
    tmp_path = file_path.with_suffix(".tmp")

    content = json.dumps(payload, indent=2, ensure_ascii=False)
    async with aiofiles.open(tmp_path, "w", encoding="utf-8") as fh:
        await fh.write(content)
    tmp_path.rename(file_path)

    return turn_artifact_uri(chat_id, turn_index)
