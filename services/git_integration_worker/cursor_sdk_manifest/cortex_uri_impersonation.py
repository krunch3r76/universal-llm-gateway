"""Locate ``cortex:`` host paths so salvage can copy them into the share.

``cortex://notes/…`` is the share URI. A workspace directory named ``cortex:``
is the 30289 leftover. ``cortex:notes/…`` (no slash after the colon) is the
historical shorthand, not a host path.
"""

from __future__ import annotations

from collections.abc import Iterable


def is_cortex_host_path_impersonation(raw: str) -> bool:
    """True when *raw* has a path component exactly ``cortex:``.

    ``cortex://…`` is a real share URI (first component is ``cortex:`` only
    because ``://`` splits that way) and is excluded. ``cortex:notes/…``
    shorthand has component ``cortex:notes``, not ``cortex:``.
    """
    text = (raw or "").strip()
    if not text:
        return False
    if text.lower().startswith("cortex://"):
        return False
    return any(part == "cortex:" for part in text.replace("\\", "/").split("/"))


def any_cortex_host_path_impersonation(paths: Iterable[str]) -> bool:
    """True when any path in *paths* is a ``cortex:`` host-path impersonation."""
    return any(is_cortex_host_path_impersonation(path) for path in paths)


def collect_cortex_impersonation_scan_paths(
    *groups: Iterable[str] | object,
) -> tuple[str, ...]:
    """Flatten path-like groups, including dropped-entry ``target`` dicts."""
    ordered: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for path in _iter_path_strings(group):
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    return tuple(ordered)


def _iter_path_strings(group: Iterable[str] | object) -> list[str]:
    if group is None:
        return []
    if isinstance(group, str):
        text = group.strip()
        return [text] if text else []
    if isinstance(group, dict):
        target = group.get("target")
        if isinstance(target, str) and target.strip():
            return [target.strip()]
        return []
    if isinstance(group, (list, tuple, set)):
        found: list[str] = []
        for item in group:
            found.extend(_iter_path_strings(item))
        return found
    return []
