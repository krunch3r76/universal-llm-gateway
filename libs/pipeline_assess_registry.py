"""
Global registry for programmatic assess handlers used by assess_loop_v1.

Placed in libs/ so both the tools handler package and any domain-specific
handler package can import it under a stable absolute path (libs/ is on PYTHONPATH).

∀ name ∈ PROGRAMMATIC_ASSESS_HANDLERS:
  fn(resolved: dict[str, Any]) → dict[str, Any] with "action" key present
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

PROGRAMMATIC_ASSESS_HANDLERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}


def register_assess_handler(
    name: str, fn: Callable[[dict[str, Any]], dict[str, Any]]
) -> None:
    """Register a programmatic assess handler under the given name."""
    PROGRAMMATIC_ASSESS_HANDLERS[name] = fn
