"""Write per-turn JSON artifacts to the workspace ``.runtime/`` tree."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from implement_admission.scheme_resolve import resolve_schemed_packet_file
from implement_admission.share_uri_emit import to_share_uri
from universal_logging import get_logger

logger = get_logger(__name__)

_ARTIFACT_SUBDIR = (".runtime", "thread-artifacts")
_ARTIFACT_NAMESPACE = "universal-llm-gateway"


def _workspace_root() -> Path:
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[7]


def resolve_artifact_path(uri: str) -> Path | None:
    """Resolve a ``workspaces://`` URI to a local path."""
    if not uri.startswith("workspaces://"):
        return None
    return resolve_schemed_packet_file(uri, workspaces_root_override=_workspace_root())


def turn_artifact_uri(chat_id: str, turn_index: int) -> str:
    rel = (
        f"{_ARTIFACT_NAMESPACE}/.runtime/thread-artifacts/"
        f"{chat_id}/turn_{turn_index:04d}.json"
    )
    return to_share_uri("workspaces", rel)


async def write_turn_artifact(
    *,
    chat_id: str,
    turn_index: int,
    payload: dict[str, Any],
) -> str:
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
