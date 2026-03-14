"""Typed dictionary contracts for Stargate debug event settings."""

from typing import TypedDict


class DebugEventConfig(TypedDict):
    """Top-level debug event settings for optional socket output."""

    socket_path: str | None
