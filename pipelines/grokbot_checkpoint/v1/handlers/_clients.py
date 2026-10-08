"""Shared helpers for grokbot-checkpoint-v1. Cortex and bus clients are the
continuity_checkpoint ones; this module does not reimplement HTTP."""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any


def _cc_clients() -> Any:
    path = (
        Path(__file__).resolve().parents[3]
        / "continuity_checkpoint"
        / "v1"
        / "handlers"
        / "_clients.py"
    )
    spec = importlib.util.spec_from_file_location("_cc_clients_for_grokbot", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load continuity checkpoint clients from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_cc = _cc_clients()
bus_get = _cc.bus_get
bus_send = _cc.bus_send
cortex_dispatch = _cc.cortex_dispatch
step_output_json = _cc.step_output_json

__all__ = [
    "bus_get",
    "bus_send",
    "cortex_dispatch",
    "DispatchFs",
    "FsClientMissingError",
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
    """Production fs has no cortex_dispatch client. Never falls back to /tmp."""

    code = "fs.client_missing"


class DispatchFs:
    """cortex_dispatch ``fs`` client. workspaces:// uses sandbox workspaces."""

    def __init__(self, dispatch: Any) -> None:
        self.dispatch = dispatch

    async def read(self, rel: str) -> bytes | None:
        return await self.read_uri(rel if "://" in rel else f"cortex://{rel}")

    async def read_uri(self, uri: str) -> bytes | None:
        if self.dispatch is None:
            raise FsClientMissingError("cortex_dispatch missing")
        if uri.startswith("workspaces://"):
            sandbox, path = "workspaces", uri.removeprefix("workspaces://")
        else:
            sandbox, path = "cortex", uri if uri.startswith("cortex://") else f"cortex://{uri}"
        result = await self.dispatch(
            "fs", {"op": "read", "sandbox": sandbox, "path": path}
        )
        if not isinstance(result, dict) or result.get("error"):
            return None
        content = result.get("content")
        if isinstance(content, bytes):
            return content
        if isinstance(content, str):
            return content.encode("utf-8")
        return None

    async def write(
        self,
        rel: str,
        data: bytes,
        *,
        expected_sha256: str | None = None,
        if_absent: bool = False,
    ) -> dict[str, Any]:
        if self.dispatch is None:
            raise FsClientMissingError("cortex_dispatch missing")
        path = rel if rel.startswith("cortex://") else f"cortex://{rel}"
        result = await self.dispatch(
            "fs",
            {
                "op": "write",
                "sandbox": "cortex",
                "path": path,
                "content": data.decode("utf-8"),
                "expected_sha256": expected_sha256 or "",
                "if_absent": if_absent,
            },
        )
        if not isinstance(result, dict) or result.get("error"):
            return {
                "ok": False,
                "conflict": True,
                "sha256": None,
                "error": result,
            }
        return {
            "ok": True,
            "conflict": False,
            "sha256": str(result.get("sha256") or file_sha256(data)),
        }


def fs_of(context: Any) -> Any:
    """Injected test fs, else the cortex_dispatch fs client. No /tmp fallback."""
    injected = getattr(context, "fs", None)
    if injected is not None:
        return injected
    dispatch = getattr(context, "cortex_dispatch", cortex_dispatch)
    if dispatch is None:
        return DispatchFs(None)
    return DispatchFs(dispatch)
