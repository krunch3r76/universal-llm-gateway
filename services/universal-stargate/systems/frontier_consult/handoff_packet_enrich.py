"""Auto-enrich MCP-seat handoff packets before validation (assertion #19650).

Merges canonical skill slugs into <invariants>, mirrors related_thread_ids as
agent_bus(fetch) steps. Platform seats load skill bodies via native triggers —
not fs(agent-skills/…) lines.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from agent_seat.inject_budget import HANDOFF_INLINE_BUDGET_BYTES
from cortex_store.guidance_entity import entity_slug_from_id
from implement_admission.admission_read import frontmatter_value
from implement_admission.skill_delivery_channels import (
    InlineBodyResolution,
    SkillInlineBudgetExceeded,
    SkillInlineResolveError,
    _outer_invariants_close_index,
    declared_inline_digests,
    enforce_inline_budget,
    format_inline_skill_block,
    parse_inline_skill_blocks,
    partition_web_skill_channels,
    replace_inline_block_for_slug,
    resolve_inline_bodies,
    text_without_inline_payload_regions,
)
from universal_event_bus import Event, event_factory
from universal_logging import get_logger

from .handoff import _extract_block
from .handoff_life_mirror import (
    WEB_RECEIVER_AGENT,
    WEB_RECIPIENT_REACHABILITY,
    RecipientReachability,
    is_life_web_receiver,
    mirror_workspaces_pointers_for_web,
)
from .handoff_web_mcp_default import apply_web_mcp_default

logger = get_logger(__name__)

__all__ = [
    "WEB_RECEIVER_AGENT",
    "WEB_RECIPIENT_REACHABILITY",
    "RecipientReachability",
    "mirror_workspaces_pointers_for_web",
    "enrich_handoff_packet",
    "has_densify_floor",
    "EnrichResult",
    "HandoffSkillInlineMaterialized",
    "HandoffSkillInlineBudgetExceeded",
]

# Always-wired densify floor for MCP-seat handoffs (claude-web + claude-cursor).
# architecture-invariants + ulg-architecture + docstring-quality are injected
# unconditionally so the cursor arch-ref floor (_missing_arch_skill_refs) is
# satisfied by enrich rather than by author hand-wiring — parity with the
# generate lane's required_skills (ULG code-work floor triple).
_DEFAULT_DENSIFY_SLUGS: tuple[str, ...] = (
    "lead-seat-boot",
    "consult-routing",
    "handoff-packet-authoring",
    "architecture-invariants",
    "ulg-architecture",
    "docstring-quality",
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

_SKILL_POINTER_LINE_RE = re.compile(
    r"(?:-\s+)?Use the `(?P<slug>[a-z0-9][-a-z0-9_]*)` skill"
    r"(?: \([^<\n]*\))?",
    re.IGNORECASE,
)

_ORIENTATION_PREFIX = (
    "Skill names for orientation (bodies inlined below — skill-inline gate):"
)


class CortexEntityReader(Protocol):
    def entity_get(self, entity_id: str, **kwargs: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class EnrichResult:
    text: str
    skills_added: list[str] = field(default_factory=list)
    skills_already_wired: list[str] = field(default_factory=list)
    skills_inlined: list[str] = field(default_factory=list)
    inline_slugs: list[str] = field(default_factory=list)
    threads_added: list[str] = field(default_factory=list)
    changed: bool = False
    inline_materialized: bool = False
    inline_total_bytes: int = 0


@event_factory
def HandoffSkillInlineMaterialized(  # noqa: N802
    request_id: str,
    packet_path: str,
    slugs: list[str],
    total_bytes: int,
) -> Event:
    return Event(
        signal="handoff.skill.inline.materialized",
        payload={
            "request_id": request_id,
            "packet_path": packet_path,
            "slugs": slugs,
            "total_bytes": total_bytes,
        },
        scope="node",
    )


@event_factory
def HandoffSkillInlineBudgetExceeded(  # noqa: N802
    request_id: str,
    packet_path: str,
    total_bytes: int,
    budget_bytes: int,
    per_slug_bytes: dict[str, int],
) -> Event:
    return Event(
        signal="handoff.skill.inline.budget.exceeded",
        payload={
            "request_id": request_id,
            "packet_path": packet_path,
            "total_bytes": total_bytes,
            "budget_bytes": budget_bytes,
            "per_slug_bytes": per_slug_bytes,
        },
        scope="node",
    )


def _canonical_skill_invariant_line(slug: str) -> str:
    """One Block-2 line: Use the `<slug>` skill (agent-bus:4888)."""
    return (
        f"- Use the `{slug}` skill "
        f"(canonical slug — seat self-fetches; ¬ fs-read skill body)"
    )


def _orientation_line(inline_slugs: tuple[str, ...]) -> str:
    return f"- {_ORIENTATION_PREFIX} {', '.join(inline_slugs)}"


def _invariants_body(text: str) -> str | None:
    open_idx = text.find("<invariants>")
    if open_idx < 0:
        return None
    close_idx = _outer_invariants_close_index(text)
    if close_idx is None or close_idx <= open_idx:
        return None
    return text[open_idx + len("<invariants>") : close_idx]


def _replace_invariants_body(text: str, body: str) -> str:
    open_idx = text.find("<invariants>")
    if open_idx < 0:
        return text
    close_idx = _outer_invariants_close_index(text)
    if close_idx is None or close_idx <= open_idx:
        return text
    return text[: open_idx + len("<invariants>")] + body + text[close_idx:]


def _packet_tag_span(text: str, tag: str) -> tuple[int, int] | None:
    """Locate outer packet tag boundaries, ignoring examples inside inlined bodies."""
    if tag == "invariants":
        open_idx = text.find("<invariants>")
        if open_idx < 0:
            return None
        close_idx = _outer_invariants_close_index(text)
        if close_idx is None or close_idx <= open_idx:
            return None
        return open_idx, close_idx + len("</invariants>")
    scan = text_without_inline_payload_regions(text)
    open_marker = f"<{tag}>"
    close_marker = f"</{tag}>"
    open_idx = scan.find(open_marker)
    if open_idx < 0:
        return None
    close_idx = scan.find(close_marker, open_idx + len(open_marker))
    if close_idx < 0:
        return None
    return open_idx, close_idx + len(close_marker)


def _packet_tag_body(text: str, tag: str) -> str | None:
    span = _packet_tag_span(text, tag)
    if span is None:
        return None
    open_idx, close_idx = span
    open_marker = f"<{tag}>"
    close_marker = f"</{tag}>"
    inner_start = open_idx + len(open_marker)
    inner_end = close_idx - len(close_marker)
    return text[inner_start:inner_end]


def _replace_packet_tag_body(text: str, tag: str, body: str) -> str:
    span = _packet_tag_span(text, tag)
    if span is None:
        return text
    open_idx, close_idx = span
    open_marker = f"<{tag}>"
    close_marker = f"</{tag}>"
    return (
        text[: open_idx + len(open_marker)]
        + body
        + text[close_idx - len(close_marker) :]
    )


def _rewrite_inline_pointer_lines(text: str, inline_slugs: set[str]) -> str:
    if not inline_slugs:
        return text

    scan = text_without_inline_payload_regions(text)
    pointer_lines = [
        match
        for match in _SKILL_POINTER_LINE_RE.finditer(scan)
        if match.group("slug").lower() in inline_slugs
    ]
    for match in reversed(pointer_lines):
        text = text[: match.start()] + text[match.end() :]
    block = _invariants_body(text)
    if block is None:
        return text
    merged = block.rstrip()
    orientation = _orientation_line(tuple(sorted(inline_slugs)))
    if orientation not in merged:
        merged = f"{merged}\n{orientation}"
    return _replace_invariants_body(text, merged)


def _existing_inline_digests(text: str) -> dict[str, str]:
    parsed = {block.slug: block.digest for block in parse_inline_skill_blocks(text)}
    for slug, digest in declared_inline_digests(text).items():
        parsed.setdefault(slug, digest)
    return parsed


def _append_inline_blocks(
    text: str,
    *,
    to_materialize: list[InlineBodyResolution],
) -> str:
    if not to_materialize:
        return text
    additions = [
        format_inline_skill_block(
            item.slug,
            source_uri=item.source_uri,
            rev=item.rev,
            body=item.body,
        )
        for item in to_materialize
    ]
    close_idx = _outer_invariants_close_index(text)
    if close_idx is None:
        return text
    insert_at = close_idx + len("</invariants>")
    return text[:insert_at] + "".join(additions) + text[insert_at:]


def _replace_stale_inline_block(text: str, item: InlineBodyResolution) -> str:
    fresh = format_inline_skill_block(
        item.slug,
        source_uri=item.source_uri,
        rev=item.rev,
        body=item.body,
    )
    return replace_inline_block_for_slug(text, item.slug, fresh)


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
    slug = entity_slug_from_id(entity_id) if entity_id else None
    return slug or None


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
        entity = cortex.entity_get(entity_id, intent="full")
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


def _pointer_slug_represented(text: str, slug: str) -> bool:
    scan = text_without_inline_payload_regions(text)
    pattern = re.compile(
        rf"- Use the `{re.escape(slug)}` skill "
        r"\(canonical slug — seat self-fetches; ¬ fs-read skill body\)",
        re.IGNORECASE,
    )
    return bool(pattern.search(scan))


def _slug_represented_in_text(text: str, slug: str) -> bool:
    """Return True when slug is already wired in canonical or legacy packet form."""
    lowered = text.lower()
    needle = slug.lower()
    if f"agent_skill:{needle}" in lowered:
        return True
    if f"`{needle}`" in lowered:
        return True
    if f"agent-skills/{needle}.md" in lowered:
        return True
    if f"/{needle}/skill.md" in lowered:
        return True
    if f"/{needle}.md" in lowered:
        return True
    for block in parse_inline_skill_blocks(text):
        if block.slug == needle:
            return True
    return False


def _merge_block_lines(
    text: str, tag: str, new_lines: list[str]
) -> tuple[str, list[str]]:
    if not new_lines:
        return text, []
    block = _packet_tag_body(text, tag)
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
    return _replace_packet_tag_body(text, tag, merged), added


def _agent_bus_fetch_line(thread_id: str) -> str:
    return (
        f"agent_bus(fetch, thread={thread_id}, last=3, compact=true) — upstream context"
    )


def _next_mcp_step_number(mcp_block: str) -> int:
    numbers = [
        int(m) for m in re.findall(r"^\s*(\d+)\.", mcp_block, flags=re.MULTILINE)
    ]
    return max(numbers, default=0) + 1


@dataclass(frozen=True, slots=True)
class _InlineMaterializeResult:
    text: str
    skills_inlined: list[str]
    pointer_skills_added: list[str]
    skills_already_wired: list[str]
    inline_slugs: list[str]
    total_bytes: int
    had_inline_slugs: bool


def _materialize_inline_skills(
    text: str,
    skill_slugs: list[str],
    *,
    budget_bytes: int,
) -> _InlineMaterializeResult:
    inline_slugs, pointer_slugs = partition_web_skill_channels(set(skill_slugs))
    if not inline_slugs:
        pointer_added: list[str] = []
        for slug in pointer_slugs:
            if not _slug_represented_in_text(text, slug):
                pointer_added.append(slug)
        if pointer_added:
            lines = [_canonical_skill_invariant_line(slug) for slug in pointer_added]
            text, _ = _merge_block_lines(text, "invariants", lines)
        already = [slug for slug in skill_slugs if slug not in pointer_added]
        return _InlineMaterializeResult(
            text=text,
            skills_inlined=[],
            pointer_skills_added=pointer_added,
            skills_already_wired=already,
            inline_slugs=[],
            total_bytes=0,
            had_inline_slugs=False,
        )
    resolved = resolve_inline_bodies(inline_slugs)
    enforce_inline_budget(resolved, budget_bytes)
    text = _rewrite_inline_pointer_lines(text, set(inline_slugs))
    existing = _existing_inline_digests(text)
    to_add: list[InlineBodyResolution] = []
    skills_inlined: list[str] = []
    for item in resolved:
        prior = existing.get(item.slug)
        if prior == item.digest:
            continue
        if prior is not None:
            text = _replace_stale_inline_block(text, item)
            skills_inlined.append(item.slug)
            continue
        to_add.append(item)
        skills_inlined.append(item.slug)
    text = _append_inline_blocks(text, to_materialize=to_add)
    pointer_added = [
        slug for slug in pointer_slugs if not _pointer_slug_represented(text, slug)
    ]
    if pointer_added:
        lines = [_canonical_skill_invariant_line(slug) for slug in pointer_added]
        text, _ = _merge_block_lines(text, "invariants", lines)
    already = [
        slug
        for slug in skill_slugs
        if slug not in skills_inlined and slug not in pointer_added
    ]
    total_bytes = sum(item.byte_len for item in resolved)
    return _InlineMaterializeResult(
        text=text,
        skills_inlined=skills_inlined,
        pointer_skills_added=pointer_added,
        skills_already_wired=already,
        inline_slugs=list(inline_slugs),
        total_bytes=total_bytes,
        had_inline_slugs=True,
    )


def enrich_handoff_packet(
    text: str,
    *,
    cortex: CortexEntityReader,
    to_agent: str | None = None,
    thread_id: str | None = None,
    skill_delivery: str | None = None,
) -> EnrichResult:
    """Non-destructively enrich an MCP-seat handoff packet (web or cursor) in memory."""
    skill_slugs = _collect_skill_slugs(text, cortex)
    inline_materialized = False
    inline_total_bytes = 0
    skills_inlined: list[str] = []

    if skill_delivery == "inline_authoritative":
        try:
            mat = _materialize_inline_skills(
                text,
                skill_slugs,
                budget_bytes=HANDOFF_INLINE_BUDGET_BYTES,
            )
        except SkillInlineBudgetExceeded:
            raise
        except SkillInlineResolveError as exc:
            raise RuntimeError(f"{exc.code}: {exc.reason}") from exc
        text = mat.text
        skills_inlined = mat.skills_inlined
        skills_added = mat.pointer_skills_added
        skills_already_wired = mat.skills_already_wired
        inline_slugs = mat.inline_slugs
        inline_materialized = mat.had_inline_slugs
        inline_total_bytes = mat.total_bytes
    else:
        inline_slugs = []
        invariant_lines: list[str] = []
        skills_added: list[str] = []
        skills_already_wired: list[str] = []
        for slug in skill_slugs:
            if _slug_represented_in_text(text, slug):
                skills_already_wired.append(slug)
            else:
                invariant_lines.append(_canonical_skill_invariant_line(slug))
                skills_added.append(slug)
        text, _ = _merge_block_lines(text, "invariants", invariant_lines)

    text, web_mcp_stamped = apply_web_mcp_default(
        text,
        to_agent=to_agent,
        current_body=_packet_tag_body(text, "mcp_capabilities"),
        replace_body=_replace_packet_tag_body,
    )

    mcp_block = _packet_tag_body(text, "mcp_capabilities") or ""
    mcp_additions: list[str] = []
    threads_added: list[str] = []

    for related_thread_id in _parse_related_thread_ids(text):
        fetch_snippet = f"agent_bus(fetch, thread={related_thread_id}"
        if fetch_snippet not in text:
            step = _next_mcp_step_number(mcp_block) + len(mcp_additions)
            mcp_additions.append(f"{step}. {_agent_bus_fetch_line(related_thread_id)}")
            threads_added.append(related_thread_id)
    text, _ = _merge_block_lines(text, "mcp_capabilities", mcp_additions)

    corpus_rewritten = False
    if is_life_web_receiver(to_agent):
        text, rewrites = mirror_workspaces_pointers_for_web(text, thread_id=thread_id)
        corpus_rewritten = bool(rewrites)

    changed = bool(
        skills_added
        or skills_inlined
        or threads_added
        or mcp_additions
        or web_mcp_stamped
        or corpus_rewritten
    )
    return EnrichResult(
        text=text,
        skills_added=skills_added,
        skills_already_wired=skills_already_wired,
        skills_inlined=skills_inlined,
        inline_slugs=inline_slugs,
        threads_added=threads_added,
        changed=changed,
        inline_materialized=inline_materialized or bool(skills_inlined),
        inline_total_bytes=inline_total_bytes,
    )


def has_densify_floor(text: str) -> bool:
    """Return True when the web densify admission floor is satisfied.

    Related-thread context must remain fetchable on every MCP-capable seat,
    including life-only web receivers.
    """
    if not _has_task_class_skill_ref(text):
        return False
    thread_ids = _parse_related_thread_ids(text)
    if thread_ids and not _has_agent_bus_fetch_for_threads(text, thread_ids):
        return False
    return True


def _has_task_class_skill_ref(text: str) -> bool:
    for slug in KNOWN_TASK_CLASS_SLUGS - frozenset(_DEFAULT_DENSIFY_SLUGS):
        if slug in text or any(
            f"{prefix}{slug}" in text for prefix in ("agent_skill:", "rule:", "skill:")
        ):
            return True
    if _bound_entity_ids(text) and "required_skills" in text:
        return True
    for slug in _DEFAULT_DENSIFY_SLUGS:
        if slug in text:
            return True
    return False


def _has_agent_bus_fetch_for_threads(text: str, thread_ids: list[str]) -> bool:
    mcp = _packet_tag_body(text, "mcp_capabilities") or text
    return all(f"thread={tid}" in mcp or f"thread: {tid}" in mcp for tid in thread_ids)
