"""Path and attribute helpers shared by implement-closeout source adapters.

Callers are live closeout adapters under ``implement_admission.closeout_adapters``.
Keeps root resolution and Stage-B done-withhold predicates out of adapter bodies.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import Source, SourceKind

_PATH_SIM_ADMIT_GATE = "path-sim-admit-gate"
_ATTENDANCE_AUTONOMOUS = "autonomous"

_SOURCE_REF_IN_PACKET = re.compile(
    r"(?:^source_ref:\s*(\S+)|<source_ref>\s*(\S+)\s*</source_ref>)",
    re.MULTILINE | re.IGNORECASE,
)
_ULG_DIRNAME = "universal-llm-gateway"
# MCP container volume target (docker/compose/mcp-server.yml). Prefer when
# present so admission reads share the same root as fs(sandbox=cortex).
_MCP_CONTAINER_CORTEX_ROOT = Path("/data/files")


def workspaces_root() -> Path:
    """Resolve the workspaces sandbox root for closeout file writes.

    Honors ``WORKSPACES_ROOT`` and nests into ``universal-llm-gateway`` when present.
    """
    raw = os.environ.get("WORKSPACES_ROOT", "/mnt/torus/projects")
    root = Path(raw).resolve()
    nested = root / _ULG_DIRNAME
    return nested if nested.is_dir() else root


def cortex_files_root() -> Path:
    """Resolve the cortex file sandbox root.

    Preference order:
    1. ``CORTEX_FILES_ROOT`` when set
    2. ``/data/files`` when that directory exists (MCP container mount;
       matches ``services/mcp-server`` ``SANDBOX_ROOT`` — friction a24515)
    3. ``~/mcp-data/files`` (host default)
    """
    raw = os.environ.get("CORTEX_FILES_ROOT")
    if raw:
        return Path(raw).resolve()
    if _MCP_CONTAINER_CORTEX_ROOT.is_dir():
        return _MCP_CONTAINER_CORTEX_ROOT.resolve()
    return (Path.home() / "mcp-data" / "files").resolve()


def source_from_ref(raw: str) -> Source:
    """Build a typed ``Source`` from a canonical or external source_ref string."""
    ref = parse_source_ref(raw)
    return Source(
        source_ref=ref.external_ref,
        canonical_ref=ref.canonical_ref,
        parent_ref=ref.parent_ref,
        selector=ref.selector,
        source_kind=SourceKind(ref.source_kind),
    )


def extract_embedded_source_ref(text: str) -> str | None:
    """Pull an embedded ``source_ref`` from packet frontmatter or XML tags."""
    match = _SOURCE_REF_IN_PACKET.search(text)
    if not match:
        return None
    return match.group(1) or match.group(2)


def plan_slug_from_ref(plan_ref: str) -> str:
    """Strip the ``plan:`` prefix from a plan entity id for filesystem paths."""
    return plan_ref.removeprefix("plan:")


def thread_id_from_bus_ref(bus_ref: str) -> str:
    """Normalize an agent-bus ref to a bare thread id (drop scheme and turn)."""
    base = bus_ref.split("#", 1)[0]
    return base.removeprefix("agent-bus:")


def decode_todo_attributes(raw: Any) -> dict[str, Any]:
    """Return a dict of todo attributes from entity_get payload forms.

    Accepts a mapping or JSON string; invalid or non-object input yields {}.
    """
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def should_withhold_stage_b_todo_done(attrs: Mapping[str, Any]) -> bool:
    """Return True when Stage-B must not flip ``workflow_state=done``.

    Autonomous path-sim and path-sim-admit-gate arcs still owe G5/G6 after
    implement-closeout; premature done confuses scoreboard/resume (a:26246).
    """
    if attrs.get("attendance") == _ATTENDANCE_AUTONOMOUS:
        return True
    if attrs.get("dispatch_lane") == _PATH_SIM_ADMIT_GATE:
        return True
    return False
