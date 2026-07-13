"""Boot `domain=` axis — todo partition, life suppression, soft reorder."""

from __future__ import annotations

from typing import Any

# Single source for coding-lane todo domain membership (F3 complement rule).
CODE_DOMAINS = frozenset(
    {"infra", "rag", "pipeline", "mcp", "model_id", "cortex", "gateway"}
)
_CODING_DOMAINS = CODE_DOMAINS
_LIFE_DOMAINS = frozenset({"personal", "legal", "financial", "employment", "health"})
_WEB_INFRA_EXCLUDE = frozenset({"infra", "rag", "pipeline", "mcp", "model_id"})
_LIFE_CONTEXTS = frozenset({"personal", "financial", "legal"})
_LIFE_ENTITY_PREFIXES = (
    "legal_matter:",
    "person:",
    "case:",
    "financial:",
    "document:",
)
_LIFE_SENTINEL_TEMPLATE = (
    "{count} life-lane items hidden — explicit life brief to view"
)


def normalize_boot_domain(domain: str | None) -> str:
    """Map caller input to coding | life | mixed-minimal."""
    if not domain or not str(domain).strip():
        return "mixed-minimal"
    key = str(domain).strip().lower().replace("_", "-")
    if key in {"coding", "code", "engineering", "dev"}:
        return "coding"
    if key in {"life", "personal", "operator"}:
        return "life"
    return "mixed-minimal"


def life_suppressed(domain: str | None, *, explicit_life_attach: bool = False) -> bool:
    """True when coding lane should hard-suppress life payloads."""
    if explicit_life_attach:
        return False
    return normalize_boot_domain(domain) == "coding"


def extend_todo_fetch_params(
    agent: str,
    todo_qs_parts: dict[str, Any],
    *,
    domain: str,
) -> None:
    """Adjust boot-todos query params from profile_dict['domain'] (in-place)."""
    _seat_parts = agent.split("-", 1)
    _platform = _seat_parts[1] if len(_seat_parts) == 2 else ""
    if domain == "coding":
        todo_qs_parts["context"] = "code"
        excluded = set(_LIFE_DOMAINS)
        if _platform == "web":
            excluded.update(_WEB_INFRA_EXCLUDE)
        todo_qs_parts["domain_exclude"] = ",".join(sorted(excluded))
    elif domain == "life":
        if _platform == "web":
            todo_qs_parts["domain_exclude"] = ",".join(sorted(_CODING_DOMAINS))
    elif _platform == "web":
        todo_qs_parts["domain_exclude"] = ",".join(sorted(_WEB_INFRA_EXCLUDE))


def _todo_domain_class(todo: dict[str, Any]) -> str:
    ctx = str(todo.get("context") or "").lower()
    dom = str(todo.get("domain") or "").lower()
    if ctx == "code" or dom in _CODING_DOMAINS:
        return "coding"
    if ctx in _LIFE_CONTEXTS or dom in _LIFE_DOMAINS:
        return "life"
    return "other"


def is_life_lane_todo(todo: dict[str, Any]) -> bool:
    return _todo_domain_class(todo) == "life"


def is_life_lane_deadline(deadline: dict[str, Any]) -> bool:
    matter_id = str(deadline.get("matter_id") or "")
    if matter_id.startswith("legal_matter:"):
        return True
    if matter_id.startswith("todo:"):
        dom = str(deadline.get("domain") or "").lower()
        if dom:
            return dom in _LIFE_DOMAINS
        return is_life_lane_todo(
            {
                "domain": deadline.get("domain"),
                "context": deadline.get("context"),
            }
        )
    return False


def is_life_lane_entity(entity_id: str | None) -> bool:
    eid = str(entity_id or "").lower()
    if any(eid.startswith(prefix) for prefix in _LIFE_ENTITY_PREFIXES):
        return True
    if eid.startswith(("todo:", "plan:", "project:", "plan_phase:", "task:")):
        return False
    return bool(eid)


def is_life_lane_temporal(row: dict[str, Any]) -> bool:
    return is_life_lane_entity(str(row.get("entity_id") or ""))


def life_lane_sentinel(count: int) -> str | None:
    if count <= 0:
        return None
    return _LIFE_SENTINEL_TEMPLATE.format(count=count)


def count_life_lane_card_items(
    *,
    todos: list[dict[str, Any]] | None,
    deadlines: list[dict[str, Any]] | None,
    temporal_active: list[dict[str, Any]] | None,
    dropbox_files: list[str] | None,
    in_flight_todos: list[dict[str, Any]] | None,
) -> int:
    hidden = 0
    if todos:
        hidden += sum(1 for t in todos if is_life_lane_todo(t))
    if deadlines:
        hidden += sum(1 for d in deadlines if is_life_lane_deadline(d))
    if temporal_active:
        hidden += sum(1 for a in temporal_active if is_life_lane_temporal(a))
    if dropbox_files:
        hidden += len(dropbox_files)
    if in_flight_todos:
        hidden += sum(1 for t in in_flight_todos if is_life_lane_todo(t))
    return hidden


def filter_life_lane_todos(
    todos: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not todos:
        return todos
    kept = [t for t in todos if not is_life_lane_todo(t)]
    return kept or None


def filter_life_lane_deadlines(
    deadlines: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if deadlines is None:
        return None
    kept = [d for d in deadlines if not is_life_lane_deadline(d)]
    return kept or None


def filter_life_lane_temporal(
    temporal_active: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    if not temporal_active:
        return temporal_active
    kept = [a for a in temporal_active if not is_life_lane_temporal(a)]
    return kept or None


def apply_domain_todo_state(
    todos: list[dict[str, Any]],
    *,
    domain: str,
    agent: str = "",
    deadlines: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Soft-reorder todos for the active domain; return cross-domain sentinel."""
    _seat_parts = agent.split("-", 1)
    _platform = _seat_parts[1] if len(_seat_parts) == 2 else ""
    if _platform == "web" and domain == "mixed-minimal":
        todos = [t for t in todos if t.get("domain") not in _WEB_INFRA_EXCLUDE]

    if domain == "coding":
        todos = [t for t in todos if not is_life_lane_todo(t)]

    if domain == "mixed-minimal":
        _seat_filtered = todos
        if not _seat_filtered:
            return todos, None
        coding = [t for t in todos if _todo_domain_class(t) == "coding"]
        life = [t for t in todos if _todo_domain_class(t) == "life"]
        other = [t for t in todos if _todo_domain_class(t) == "other"]
        merged: list[dict[str, Any]] = []
        for bucket in (coding[:2], life[:2], other[:1]):
            for t in bucket:
                if t not in merged:
                    merged.append(t)
        for t in todos:
            if t not in merged and len(merged) < 5:
                merged.append(t)
        return merged or todos, None

    primary_key = domain
    other_key = "life" if domain == "coding" else "coding"
    primary = [t for t in todos if _todo_domain_class(t) == primary_key]
    other = [t for t in todos if _todo_domain_class(t) == other_key]
    remainder = [t for t in todos if _todo_domain_class(t) not in {primary_key, other_key}]
    ordered = primary + remainder
    if not ordered:
        ordered = todos

    sentinel: str | None = None
    if domain == "coding":
        return ordered, None
    if other or (deadlines and domain in {"coding", "life"}):
        parts: list[str] = []
        if other:
            parts.append(f"{len(other)} todo(s) hidden")
        if deadlines:
            parts.append(f"{len(deadlines)} deadline(s) in other domain")
        if parts:
            sentinel = f"other-domain: {', '.join(parts)}"
    return ordered, sentinel
