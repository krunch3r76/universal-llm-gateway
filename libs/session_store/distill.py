"""Heuristic Index line distillation."""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def distill_index_line(
    turn_n: int,
    role: str,
    body: str,
    *,
    max_chars: int = 120,
) -> str:
    collapsed = _WS.sub(" ", body.replace("\r\n", "\n").replace("\r", "\n")).strip()
    prefix = f"{turn_n:04d} {role}: "
    room = max_chars - len(prefix)
    if room < 1:
        return prefix[:max_chars]
    if len(collapsed) <= room:
        return prefix + collapsed
    return prefix + collapsed[: room - 1] + "…"
