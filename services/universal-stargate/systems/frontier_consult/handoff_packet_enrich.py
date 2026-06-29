"""Auto-enrich web handoff packets before validation (assertion #19650).

Resolves todo required_skills + default densify slugs via entity_get → source_uri,
merges translated fs lines into <invariants>/<mcp_capabilities>, and mirrors
related_thread_ids as agent_bus(fetch) steps.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from implement_admission.admission_read import frontmatter_value
from implement_admission.skill_fs_line import (
    source_uri_to_fs_line as _canonical_source_uri_to_fs_line,
)
from universal_logging import get_logger

from .handoff import _extract_block

logger = get_logger(__name__)

WEB_RECEIVER_AGENT = "claude-web"

# Always-wired densify floor for MCP-seat handoffs (claude-web + claude-cursor).
# architecture-invariants + ulg-architecture are injected unconditionally so the
# cursor arch-ref floor (_missing_arch_skill_refs) is satisfied by enrich rather
# than by author hand-wiring — parity with the generate lane's required_skills.
_DEFAULT_DENSIFY_SLUGS: tuple[str, ...] = (
    "lead-seat-boot",
    "consult-routing",
    "handoff-packet-authoring",
    "architecture-invariants",
    "ulg-architecture",
)

_TASK_CLASS_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("mcp", "routing", "surface", "stargate"), "mcp-surface-change"),
    (("observability", "event", "debug", "forensic"), "debug-with-events"),
    (("pipeline",), "build-pipeline"),
    (("modular", "sloc"), "modularize-discipline"),
    (("service", "restart", "lifecycle", "manage"), "service-lifecycle"),
    (("dispatch", "poll", "handoff"), "dispatch-shape"),
    (("cursor-sdk", "sdk", "composer"), "cursor-sdk-instruction-standard"),
)

KNOWN_TASK_CLASS_SLUGS: frozenset[str] = frozenset(
    {
        "mcp-surface-change",
        "modularize-discipline",
        "implement-todo",
        "implementation-plan-workflow",
        "build-pipeline",
        "refine-pipeline",
        "debug-with-events",
        "cursor-sdk-instruction-standard",
        "service-lifecycle",
        "dispatch-shape",
        "lead-seat-boot",
        "consult-routing",
    }
)

_SKILL_SUGGEST_STEP = (
    'skill_suggest(conversation_context="<task summary from packet scope>")'
)


class CortexEntityReader(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class EnrichResult:
    text: str
    skills_added: list[str] = field(default_factory=list)
    skills_already_wired: list[str] = field(default_factory=list)
    threads_added: list[str] = field(default_factory=list)
    changed: bool = False


def source_uri_to_fs_line(source_uri: str) -> str:
    """Map agent_skill.source_uri to a packet fs load line (enrich positional form)."""
    return _canonical_source_uri_to_fs_line(
        source_uri, op="read", fs_call_style="positional"
    )


def _parse_related_thread_ids(text: str) -> list[str]:
    region_match = re.match(r"^---\n(.*?)\n---", text, flags=re.DOTALL)
    if not region_match:
        return []
    region = region_match.group(1)
    list_match = re.search(r"related_thread_ids:\s*(\[[^\]]*\])", region)
    if list_match:
        try:
            raw = json.loads(list_match.group(1).replace("'", '"'))
        except json.JSONDecodeError:
            return []
        return [str(item).strip() for item in raw if str(item).strip()]
    scalar = re.search(r"related_thread_ids:\s*(\S+)", region)
    if scalar:
        return [scalar.group(1).strip().strip('"').strip("'")]
    return []


def _canonical_entity_id(raw: str, prefix: str) -> str:
    return raw if raw.startswith(f"{prefix}:") else f"{prefix}:{raw}"


def _bound_entity_ids(text: str) -> list[str]:
    ids: list[str] = []
    todo_raw = frontmatter_value(text, "todo")
    if todo_raw:
        ids.append(_canonical_entity_id(todo_raw.strip(), "todo"))
    task_raw = frontmatter_value(text, "task")
    if task_raw:
        ids.append(_canonical_entity_id(task_raw.strip(), "task"))
    return ids


def _todo_entity_id(text: str) -> str | None:
    ids = _bound_entity_ids(text)
    for entity_id in ids:
        if entity_id.startswith("todo:"):
            return entity_id
    return None


def _skill_slug_from_entity(entity: dict[str, Any]) -> str | None:
    attrs = entity.get("attributes") or {}
    if isinstance(attrs, dict):
        source_uri = attrs.get("source_uri")
        if source_uri:
            return None
    entity_id = str(entity.get("id") or entity.get("entity_id") or "")
    if entity_id.startswith("agent_skill:"):
        return entity_id.removeprefix("agent_skill:")
    return None


def _resolve_source_uri(cortex: CortexEntityReader, slug: str) -> str | None:
    try:
        entity = cortex.entity_get(f"agent_skill:{slug}")
    except Exception as exc:
        logger.warning("enrich entity_get failed slug=%s error=%s", slug, exc)
        return None
    top = entity.get("source_uri")
    if top and str(top).strip():
        return str(top).strip()
    attrs = entity.get("attributes") or {}
    if not isinstance(attrs, dict):
        return None
    raw = attrs.get("source_uri")
    return str(raw).strip() if raw else None


def _heuristic_task_class_slugs(text: str) -> list[str]:
    haystack = " ".join(
        filter(
            None,
            (
                _extract_block(text, "scope") or "",
                frontmatter_value(text, "active_project_tag") or "",
            ),
        )
    ).lower()
    found: list[str] = []
    for keywords, slug in _TASK_CLASS_KEYWORDS:
        if any(kw in haystack for kw in keywords) and slug not in found:
            found.append(slug)
    return found


def _entity_required_skills(cortex: CortexEntityReader, entity_id: str) -> list[str]:
    try:
        entity = cortex.entity_get(entity_id)
    except Exception as exc:
        logger.warning("enrich entity_get failed id=%s error=%s", entity_id, exc)
        return []
    attrs = entity.get("attributes") or {}
    if not isinstance(attrs, dict):
        return []
    raw = attrs.get("required_skills")
    if not isinstance(raw, list):
        return []
    return [str(s).strip() for s in raw if str(s).strip()]


def _collect_skill_slugs(text: str, cortex: CortexEntityReader) -> list[str]:
    slugs: list[str] = list(_DEFAULT_DENSIFY_SLUGS)
    for entity_id in _bound_entity_ids(text):
        for slug in _entity_required_skills(cortex, entity_id):
            if slug not in slugs:
                slugs.append(slug)
    for slug in _heuristic_task_class_slugs(text):
        if slug not in slugs:
            slugs.append(slug)
    return slugs


def _merge_block_lines(
    text: str, tag: str, new_lines: list[str]
) -> tuple[str, list[str]]:
    if not new_lines:
        return text, []
    block = _extract_block(text, tag)
    if block is None:
        return text, []
    added: list[str] = []
    merged = block.rstrip()
    for line in new_lines:
        stripped = line.strip()
        if not stripped or stripped in merged:
            continue
        merged = f"{merged}\n{stripped}"
        added.append(stripped)
    if not added:
        return text, []
    replacement = f"<{tag}>{merged}</{tag}>"
    pattern = rf"<{tag}>.*?</{tag}>"
    return re.sub(pattern, replacement, text, count=1, flags=re.DOTALL), added


def _agent_bus_fetch_line(thread_id: str) -> str:
    return (
        f"agent_bus(fetch, thread={thread_id}, last=3, compact=true) — upstream context"
    )


def _next_mcp_step_number(mcp_block: str) -> int:
    numbers = [
        int(m) for m in re.findall(r"^\s*(\d+)\.", mcp_block, flags=re.MULTILINE)
    ]
    return max(numbers, default=0) + 1


def enrich_handoff_packet(
    text: str,
    *,
    cortex: CortexEntityReader,
) -> EnrichResult:
    """Non-destructively enrich an MCP-seat handoff packet (web or cursor) in memory."""
    skill_slugs = _collect_skill_slugs(text, cortex)
    invariant_lines: list[str] = []
    skills_added: list[str] = []
    skills_already_wired: list[str] = []
    for slug in skill_slugs:
        source_uri = _resolve_source_uri(cortex, slug)
        if not source_uri:
            continue
        try:
            line = source_uri_to_fs_line(source_uri)
        except ValueError:
            continue
        if line not in text:
            invariant_lines.append(f"- {line}  # agent_skill:{slug}")
            skills_added.append(slug)
        else:
            skills_already_wired.append(slug)

    text, _ = _merge_block_lines(text, "invariants", invariant_lines)

    mcp_block = _extract_block(text, "mcp_capabilities") or ""
    mcp_additions: list[str] = []
    threads_added: list[str] = []

    if "skill_suggest" not in mcp_block.lower():
        step = _next_mcp_step_number(mcp_block)
        mcp_additions.append(f"{step}. {_SKILL_SUGGEST_STEP}")

    for thread_id in _parse_related_thread_ids(text):
        fetch_snippet = f"agent_bus(fetch, thread={thread_id}"
        if fetch_snippet not in text:
            step = _next_mcp_step_number(mcp_block) + len(mcp_additions)
            mcp_additions.append(f"{step}. {_agent_bus_fetch_line(thread_id)}")
            threads_added.append(thread_id)

    text, _ = _merge_block_lines(text, "mcp_capabilities", mcp_additions)
    changed = bool(skills_added or threads_added or mcp_additions)
    return EnrichResult(
        text=text,
        skills_added=skills_added,
        skills_already_wired=skills_already_wired,
        threads_added=threads_added,
        changed=changed,
    )


def has_densify_floor(text: str) -> bool:
    """Return True when the web densify admission floor is satisfied."""
    if not _has_task_class_skill_ref(text):
        return False
    thread_ids = _parse_related_thread_ids(text)
    if thread_ids and not _has_agent_bus_fetch_for_threads(text, thread_ids):
        return False
    return True


def _has_task_class_skill_ref(text: str) -> bool:
    for slug in KNOWN_TASK_CLASS_SLUGS - frozenset(_DEFAULT_DENSIFY_SLUGS):
        if slug in text or f"agent_skill:{slug}" in text:
            return True
    if _bound_entity_ids(text) and "required_skills" in text:
        return True
    for slug in _DEFAULT_DENSIFY_SLUGS:
        if slug in text:
            return True
    return False


def _has_agent_bus_fetch_for_threads(text: str, thread_ids: list[str]) -> bool:
    mcp = _extract_block(text, "mcp_capabilities") or text
    return all(f"thread={tid}" in mcp or f"thread: {tid}" in mcp for tid in thread_ids)
