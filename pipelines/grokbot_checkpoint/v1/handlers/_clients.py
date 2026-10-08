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
    "CortexFs",
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


class CortexFs:
    """Byte store keyed by cortex-relative path. Tests replace ``context.fs``."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path("/tmp/grokbot-checkpoint-fs")

    def _path(self, rel: str) -> Path:
        if not rel_path_ok(rel):
            raise ValueError(f"path escape: {rel}")
        path = (self.root / rel).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"path escape: {rel}")
        return path

    def read(self, rel: str) -> bytes | None:
        path = self._path(rel)
        if not path.is_file():
            return None
        return path.read_bytes()

    def write(
        self, rel: str, data: bytes, *, expected_sha256: str | None = None
    ) -> dict[str, Any]:
        path = self._path(rel)
        current = path.read_bytes() if path.is_file() else None
        current_sha = file_sha256(current) if current is not None else None
        if expected_sha256 is not None and current_sha != expected_sha256:
            return {"ok": False, "conflict": True, "sha256": current_sha}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"ok": True, "conflict": False, "sha256": file_sha256(data)}


def fs_of(context: Any) -> Any:
    fs = getattr(context, "fs", None)
    if fs is None:
        fs = CortexFs()
        context.fs = fs
    return fs
