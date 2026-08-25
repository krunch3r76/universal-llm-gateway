"""Shared attention helpers — idle escalation, age fill, compact seconds."""

from __future__ import annotations

from dataclasses import replace

from .dtos import AttentionItem


def escalate(age_ms: int | None, warn_ms: int, crit_ms: int) -> str | None:
    """Return the severity ``age_ms`` earns, or ``None`` if under the warn floor."""
    if age_ms is None:
        return None
    if age_ms >= crit_ms:
        return "crit"
    if age_ms >= warn_ms:
        return "warn"
    return None


def secs(age_ms: int | None) -> str:
    """Render an age in whole seconds for operator-facing detail text."""
    return "unknown" if age_ms is None else f"{age_ms // 1000}s"


def fill_ages(items: list[AttentionItem], now_ms: int) -> list[AttentionItem]:
    """Stamp ``age_ms`` from ``since_ms`` at derive time (View must not latch)."""
    filled: list[AttentionItem] = []
    for item in items:
        if item.since_ms is None:
            filled.append(item)
            continue
        age = max(0, now_ms - item.since_ms)
        if item.age_ms == age:
            filled.append(item)
            continue
        filled.append(replace(item, age_ms=age))
    return filled
