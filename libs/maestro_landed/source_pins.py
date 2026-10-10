"""Verify compiled-source shas before computing a restart set (AC6)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def _cortex_rel(uri: str) -> str:
    for prefix in ("cortex://", "cortex:/"):
        if uri.startswith(prefix):
            return uri.removeprefix(prefix).lstrip("/")
    return uri.lstrip("/")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def verify_source_pins(
    source_pins: dict[str, Any],
    *,
    files_root: Path | None = None,
) -> tuple[bool, dict[str, str]]:
    """Return (ok, details) with live sha per pin key."""
    root = files_root or Path(os.environ.get("CORTEX_FILES_ROOT", "/mnt/torus/mcp-data/files"))
    details: dict[str, str] = {}
    ok = True
    for key, spec in source_pins.items():
        if not isinstance(spec, dict):
            ok = False
            details[key] = "invalid_pin_spec"
            continue
        uri = str(spec.get("uri") or "")
        expected = str(spec.get("read_sha256") or "").lower()
        rel = _cortex_rel(uri)
        path = root / rel
        if not path.is_file():
            ok = False
            details[key] = "missing"
            continue
        live = file_sha256(path)
        details[key] = live
        if expected and live != expected:
            ok = False
    return ok, details
