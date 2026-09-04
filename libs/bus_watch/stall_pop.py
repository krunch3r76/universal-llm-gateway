"""Machine-readable stall-pop line for IDE notify_on_output."""

from __future__ import annotations

import sys
from typing import TextIO

_STALL_POP_PREFIX = "stall-pop:"


def emit_stall_pop(reason: str, *, stream: TextIO | None = None) -> None:
    """Print machine line ``stall-pop: <reason>`` flush=True for notify_on_output."""
    out = stream or sys.stdout
    text = reason.strip()
    print(f"{_STALL_POP_PREFIX} {text}", flush=True, file=out)
