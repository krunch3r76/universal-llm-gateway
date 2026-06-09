"""Shared helpers for implement closeout adapters."""

from __future__ import annotations

import os
import re
from pathlib import Path

from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import Source, SourceKind

_SOURCE_REF_IN_PACKET = re.compile(
    r"(?:^source_ref:\s*(\S+)|<source_ref>\s*(\S+)\s*</source_ref>)",
    re.MULTILINE | re.IGNORECASE,
)
_ULG_DIRNAME = "universal-llm-gateway"


def workspaces_root() -> Path:
    raw = os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
    root = Path(raw).resolve()
    nested = root / _ULG_DIRNAME
    return nested if nested.is_dir() else root


def cortex_files_root() -> Path:
    raw = os.environ.get("CORTEX_FILES_ROOT")
    if raw:
        return Path(raw).resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


def source_from_ref(raw: str) -> Source:
    ref = parse_source_ref(raw)
    return Source(
        source_ref=ref.external_ref,
        canonical_ref=ref.canonical_ref,
        parent_ref=ref.parent_ref,
        selector=ref.selector,
        source_kind=SourceKind(ref.source_kind),
    )


def extract_embedded_source_ref(text: str) -> str | None:
    match = _SOURCE_REF_IN_PACKET.search(text)
    if not match:
        return None
    return match.group(1) or match.group(2)


def plan_slug_from_ref(plan_ref: str) -> str:
    return plan_ref.removeprefix("plan:")


def thread_id_from_bus_ref(bus_ref: str) -> str:
    base = bus_ref.split("#", 1)[0]
    return base.removeprefix("agent-bus:")
