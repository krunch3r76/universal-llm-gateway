"""Porcelain gate: first-column M/D on a touched path stops the restart set."""

from __future__ import annotations


def porcelain_blocks(
    porcelain_raw: str,
    touched_paths: list[str],
) -> list[str]:
    """Paths with first-column M or D in porcelain that intersect touched_paths."""
    touched = {p.replace("\\", "/") for p in touched_paths}
    blocked: list[str] = []
    for line in porcelain_raw.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        if not any(ch in ("M", "D") for ch in status):
            continue
        path = line[3:].strip().replace("\\", "/")
        if path in touched:
            blocked.append(path)
    return sorted(set(blocked))
