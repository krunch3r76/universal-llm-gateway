"""Skill-binding audit detectors — manifest attribute presence and tool registry.

Post thread-1067 backfill: every live ``agent_skill`` should carry
``attributes.skill_binding``; ``tool_manual`` rows must reference tools/domains
that exist in canonical.yaml or the MCP tools registry.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from universal_logging import WARNING, get_logger

from ...db import query
from ._shared import _finding

logger = get_logger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[4]
_CANONICAL_PATH = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"
_MCP_TOOLS_DIR = _REPO_ROOT / "services" / "mcp-server" / "tools"

_PRIVATE_TOOL_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]*$")
_NAME_KWARG_RE = re.compile(r'@mcp\.tool\([^)]*name=["\']([^"\']+)["\']')
_TOOL_FUNC_RE = re.compile(r"@mcp\.tool\b")
_DEF_NAME_RE = re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\(")


def _load_canonical_domain_sets() -> tuple[set[str], set[str]]:
    try:
        import yaml
    except ImportError:
        logger.log(WARNING, "PyYAML unavailable — skill_binding_tool_unknown degraded")
        return set(), set()
    if not _CANONICAL_PATH.is_file():
        logger.log(WARNING, "canonical.yaml missing at %s", _CANONICAL_PATH)
        return set(), set()
    try:
        data = yaml.safe_load(_CANONICAL_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.log(WARNING, "canonical.yaml parse failed: %s", exc)
        return set(), set()
    if not isinstance(data, dict):
        return set(), set()
    domains = data.get("domains") or []
    primary = {d for d in domains if isinstance(d, str)}
    overflow_extra: set[str] = set()
    tools = data.get("tools")
    if isinstance(tools, list):
        for entry in tools:
            if isinstance(entry, dict):
                domain = entry.get("domain")
                if isinstance(domain, str):
                    overflow_extra.add(domain)
    return primary, primary | overflow_extra


def _load_mcp_tool_stems() -> set[str]:
    if not _MCP_TOOLS_DIR.is_dir():
        logger.log(WARNING, "MCP tools dir missing at %s", _MCP_TOOLS_DIR)
        return set()
    stems: set[str] = set()
    for path in _MCP_TOOLS_DIR.glob("*.py"):
        name = path.stem
        if name == "__init__" or name.startswith("_"):
            continue
        stems.add(name)
    return stems


def _load_mcp_registered_tool_names() -> set[str]:
    if not _MCP_TOOLS_DIR.is_dir():
        logger.log(WARNING, "MCP tools dir missing at %s", _MCP_TOOLS_DIR)
        return set()
    names: set[str] = set()
    for path in _MCP_TOOLS_DIR.rglob("*.py"):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            logger.log(WARNING, "Failed to read MCP tool file %s: %s", path, exc)
            continue
        for i, line in enumerate(lines):
            if not _TOOL_FUNC_RE.search(line):
                continue
            name_match = _NAME_KWARG_RE.search(line)
            if name_match:
                names.add(name_match.group(1))
                continue
            for following in lines[i + 1 :]:
                def_match = _DEF_NAME_RE.match(following)
                if def_match:
                    names.add(def_match.group(1))
                    break
    return names


_PRIMARY_DOMAINS, _OVERFLOW_DOMAINS = _load_canonical_domain_sets()
_MCP_TOOL_STEMS = _load_mcp_tool_stems()
_MCP_REGISTERED_TOOL_NAMES = _load_mcp_registered_tool_names()


def _parse_attributes(raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None


def _tool_is_valid(name: str, exposure: str) -> bool:
    if exposure == "primary":
        return name in _PRIMARY_DOMAINS
    if exposure == "overflow":
        return (
            name in _OVERFLOW_DOMAINS
            or name in _MCP_TOOL_STEMS
            or name in _MCP_REGISTERED_TOOL_NAMES
        )
    if exposure == "private":
        return bool(name) and _PRIVATE_TOOL_RE.match(name) is not None
    return False


def detect_skill_binding_missing(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """Live agent_skill entities without attributes.skill_binding."""
    sql = """
        SELECT id FROM entities
        WHERE type = 'agent_skill'
          AND (status IS NULL OR status != 'deprecated')
          AND json_extract(attributes, '$.skill_binding') IS NULL
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    return [
        _finding(
            "skill_binding_missing",
            r["id"],
            "agent_skill missing skill_binding attribute — tag per "
            "agent-skills/skill-document-writing.md v2.0",
        )
        for r in rows
    ]


def detect_skill_binding_tool_unknown(
    conn, subject: str | None = None
) -> list[dict[str, Any]]:
    """tool_manual skill_binding rows whose bound_tools fail registry checks."""
    sql = """
        SELECT id, attributes FROM entities
        WHERE type = 'agent_skill'
          AND json_extract(attributes, '$.skill_binding.skill_class') = 'tool_manual'
          AND json_extract(attributes, '$.skill_binding.tool_binding') IS NOT NULL
    """
    params: tuple = ()
    if subject:
        sql += " AND id = ?"
        params = (subject,)
    rows = query(conn, sql, params)
    findings: list[dict[str, Any]] = []
    for r in rows:
        attrs = _parse_attributes(r.get("attributes"))
        if not attrs:
            continue
        binding = attrs.get("skill_binding")
        if not isinstance(binding, dict):
            continue
        tool_binding = binding.get("tool_binding")
        if not isinstance(tool_binding, dict):
            continue
        exposure = tool_binding.get("exposure")
        if not isinstance(exposure, str):
            continue
        bound_tools = tool_binding.get("bound_tools")
        if not isinstance(bound_tools, list):
            continue
        entity_id = r["id"]
        for entry in bound_tools:
            if not isinstance(entry, str) or not entry.strip():
                findings.append(
                    _finding(
                        "skill_binding_tool_unknown",
                        entity_id,
                        f"bound tool {entry!r} (exposure={exposure}) not found in registry",
                        audit_id=f"skill_binding_tool_unknown:{entity_id}:{entry}",
                    )
                )
                continue
            name = entry.strip()
            if _tool_is_valid(name, exposure):
                continue
            findings.append(
                _finding(
                    "skill_binding_tool_unknown",
                    entity_id,
                    f"bound tool {name!r} (exposure={exposure}) not found in registry",
                    audit_id=f"skill_binding_tool_unknown:{entity_id}:{name}",
                )
            )
    return findings


__all__ = [
    "detect_skill_binding_missing",
    "detect_skill_binding_tool_unknown",
]
