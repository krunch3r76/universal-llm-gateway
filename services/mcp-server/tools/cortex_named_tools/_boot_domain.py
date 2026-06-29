"""Boot `domain=` axis — todo partition, soft reorder, cross-domain sentinel."""

from __future__ import annotations

from typing import Any

_CODING_DOMAINS = frozenset(
    {"infra", "rag", "pipeline", "mcp", "model_id", "cortex", "gateway"}
)
_LIFE_DOMAINS = frozenset({"personal", "legal", "financial", "employment", "health"})
_WEB_INFRA_EXCLUDE = frozenset({"infra", "rag", "pipeline", "mcp", "model_id"})
_LIFE_CONTEXTS = frozenset({"personal", "financial", "legal"})


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
        if _platform == "web":
            todo_qs_parts["domain_exclude"] = ",".join(sorted(_WEB_INFRA_EXCLUDE))
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
    if other or (deadlines and domain in {"coding", "life"}):
        parts: list[str] = []
        if other:
            parts.append(f"{len(other)} todo(s) hidden")
        if deadlines:
            parts.append(f"{len(deadlines)} deadline(s) in other domain")
        if parts:
            sentinel = f"other-domain: {', '.join(parts)}"
    return ordered, sentinel
