"""Whole-section shed for hop checkpoint render — link lines instead of mid-body cuts."""

from __future__ import annotations

from collections.abc import Callable

SECTION_LINKED_MARKER = "[section_linked"

# Lowest-priority sections shed first (unknown trailing sections last).
_DEFAULT_SHED_ORDER: tuple[str, ...] = (
    "FIRST ACTS",
    "OPEN FOR KAYWAN",
    "OWED / NEXT",
    "LAST LEG",
    "NOTES",
    "__preamble__",
)


def _utf8_len(text: str) -> int:
    return len((text or "").encode("utf-8"))


def section_md_read_link(*, uri: str, section: str, size_b: int) -> str:
    """One resolvable link line replacing a shed section body."""
    safe_section = section.replace('"', "'")
    return (
        f"{SECTION_LINKED_MARKER} {section} {size_b}/{size_b}] "
        f'`fs(op="md_read", section="{safe_section}", path="{uri}")`'
    )


def legacy_checkpoint_link(*, uri: str, size_b: int) -> str:
    return (
        f"{SECTION_LINKED_MARKER} checkpoint {size_b}/{size_b}] "
        f"`fs(op=\"md_read\", path=\"{uri}\")`"
    )


def shed_sections_to_budget(
    sections: dict[str, str],
    *,
    budget_b: int,
    uri: str,
    assemble: Callable[[dict[str, str]], str],
    shed_order: tuple[str, ...] = _DEFAULT_SHED_ORDER,
    never_shed: frozenset[str] = frozenset({"IN FLIGHT"}),
) -> tuple[dict[str, str], frozenset[str]]:
    """Replace whole sections with link lines until assembled UTF-8 fits *budget_b*."""
    working = dict(sections)
    linked: set[str] = set()

    def _fits() -> bool:
        return _utf8_len(assemble(working)) <= budget_b

    if _fits():
        return working, frozenset()

    candidates: list[str] = []
    for name in shed_order:
        if name in working and working[name] and name not in never_shed:
            candidates.append(name)
    for name in sorted(working):
        if (
            name not in candidates
            and name not in never_shed
            and working.get(name)
            and not name.startswith("__")
        ):
            candidates.append(name)
    if "__preamble__" in working and "__preamble__" not in candidates:
        if working["__preamble__"] and "__preamble__" not in never_shed:
            candidates.append("__preamble__")

    for name in reversed(candidates):
        if _fits():
            break
        body = working.get(name, "")
        if not body or name in linked:
            continue
        size = _utf8_len(body)
        display = "preamble" if name == "__preamble__" else name
        working[name] = section_md_read_link(uri=uri, section=display, size_b=size)
        linked.add(name)

    return working, frozenset(linked)


__all__ = [
    "SECTION_LINKED_MARKER",
    "legacy_checkpoint_link",
    "section_md_read_link",
    "shed_sections_to_budget",
]