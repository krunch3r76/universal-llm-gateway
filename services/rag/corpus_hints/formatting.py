"""Format corpus hints and register vocabulary for prompt injection."""

from __future__ import annotations

__all__ = ["format_register_hints", "get_hints_for_scopes"]


def format_register_hints(
    vocabulary: dict[str, dict[str, list[str]]],
    scopes: list[str] | None = None,
) -> str:
    """Format register-structured vocabulary for prompt injection."""
    if not vocabulary:
        return ""
    target = (
        vocabulary
        if not scopes
        else {s: vocabulary[s] for s in scopes if s in vocabulary}
    )
    if not target:
        return ""
    lines: list[str] = []
    for scope, registers in sorted(target.items()):
        parts: list[str] = []
        for reg, terms in sorted(registers.items()):
            if terms:
                parts.append(f"{reg}: {', '.join(terms)}")
        if parts:
            lines.append(f"[{scope}] {' | '.join(parts)}")
    return "\n".join(lines)


def get_hints_for_scopes(
    hints: dict[str, str],
    scopes: list[str] | None = None,
) -> str:
    """Format hints for the given scope(s) as a single comma-separated line."""
    if not hints:
        return ""
    all_hints = ", ".join(v for v in hints.values() if v)
    if not scopes or scopes == ["both"]:
        return all_hints
    parts = [hints[scope] for scope in scopes if scope in hints and hints[scope]]
    return ", ".join(parts) if parts else all_hints
