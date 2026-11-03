"""Interlocutor facade — read/steer over Cowork and display :8."""

from interlocutor.facade import execute_read, execute_steer
from interlocutor.mcp_tool import register_interlocutor_tool

# Harvest nominates these manage slugs when this lib lands (package-grain).
CONSUMERS: tuple[str, ...] = ("mcp",)

__all__ = [
    "CONSUMERS",
    "execute_read",
    "execute_steer",
    "register_interlocutor_tool",
]
