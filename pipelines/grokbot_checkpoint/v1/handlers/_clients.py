"""Shared helpers for grokbot-checkpoint-v1.

Bus and cortex_dispatch come from ``continuity_tape.checkpoint_http``.
File bytes use cortex_files_root / workspaces_root, not a cortex tool.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from continuity_tape.checkpoint_http import (
    bus_get,
    bus_send,
    cortex_dispatch,
    step_output_json,
)

__all__ = [
    "bus_get",
    "bus_send",
    "cortex_dispatch",
    "FsClientMissingError",
    "RootFs",
    "file_sha256",
    "fs_of",
    "refuse",
    "rel_path_ok",
    "step_output_json",
]


def refuse(code: str, message: str) -> dict[str, Any]:
    return {"refused": {"code": code, "message": message}}


def rel_path_ok(path: str) -> bool:
    """Cortex-relative paths reject absolute paths and ``..`` segments."""
    text = (path or "").strip()
    if not text or text.startswith(("/", "\\")) or ":" in text.split("/")[0]:
        return False
    parts = [p for p in text.replace("\\", "/").split("/") if p != ""]
    return ".." not in parts and parts


def file_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def dumps(obj: Any) -> bytes:
    return json.dumps(
        obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


class FsClientMissingError(Exception):
    """Share path escaped its root. cortex_files_root() itself does not raise:
    it uses CORTEX_FILES_ROOT, else /data/files, else ~/mcp-data/files
    (implement_admission.closeout_helpers), matching cortex_store and the MCP mount.
    RootFs writes are check-then-write on that directory. They are not atomic and
    they do not emit cortex fs index events. The concurrency key serializes one
    dispatch thread; two threads can still race a shared card.
    """

    code = "fs.client_missing"


_ULG = "universal-llm-gateway"


def _share_path(uri: str) -> Path:
    """Same root split as cursor_paste_resolve read_share_uri."""
    from implement_admission.closeout_helpers import cortex_files_root, workspaces_root

    text = (uri or "").strip()
    if text.startswith("workspaces://"):
        projects = workspaces_root().resolve()
        if projects.name == _ULG:
            projects = projects.parent
        root = projects
        path = (root / text.removeprefix("workspaces://")).resolve()
    else:
        rel = text.removeprefix("cortex://")
        root = cortex_files_root().resolve()
        path = (root / rel).resolve()
    path.relative_to(root)
    return path


class RootFs:
    """File sandbox via cortex_files_root / workspaces_root. Not a cortex tool."""

    async def read(self, rel: str) -> bytes | None:
        return await self.read_uri(rel if "://" in rel else f"cortex://{rel}")

    async def read_uri(self, uri: str) -> bytes | None:
        try:
            path = _share_path(uri)
        except (ValueError, OSError) as exc:
            raise FsClientMissingError(str(exc)) from exc
        if not path.is_file():
            return None
        return path.read_bytes()

    async def list(self, rel: str) -> list[str] | None:
        """Cortex-relative file paths under ``rel``, or None when it is not a directory."""
        try:
            path = _share_path(rel if "://" in rel else f"cortex://{rel}")
            root = _share_path("cortex://")
        except (ValueError, OSError) as exc:
            raise FsClientMissingError(str(exc)) from exc
        if not path.is_dir():
            return None
        found: list[str] = []
        for child in path.iterdir():
            if child.is_file():
                found.append(str(child.relative_to(root)))
        return sorted(found)

    async def write(
        self,
        rel: str,
        data: bytes,
        *,
        expected_sha256: str | None = None,
        if_absent: bool = False,
    ) -> dict[str, Any]:
        try:
            path = _share_path(rel if "://" in rel else f"cortex://{rel}")
        except (ValueError, OSError) as exc:
            raise FsClientMissingError(str(exc)) from exc
        if if_absent and path.is_file():
            return {"ok": False, "conflict": True, "sha256": file_sha256(path.read_bytes())}
        if expected_sha256 and path.is_file():
            live = file_sha256(path.read_bytes())
            if live != expected_sha256:
                return {"ok": False, "conflict": True, "sha256": live}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"ok": True, "conflict": False, "sha256": file_sha256(data)}


def fs_of(context: Any) -> Any:
    """Injected test fs, else the share-root client. No /tmp fallback."""
    injected = getattr(context, "fs", None)
    if injected is not None:
        return injected
    return RootFs()
