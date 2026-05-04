"""Shared type definitions for event-store named operations.

This module contains only static data shapes used by the operation catalog and
execution dispatcher. It intentionally avoids registry state and implementation
imports so operation metadata can be imported without creating cycles.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypedDict

if TYPE_CHECKING:
    from .store import EventStore


class ParamDef(TypedDict, total=False):
    """Discovery metadata for a single named-operation parameter.

    The metadata is descriptive rather than authoritative: handlers still own
    runtime coercion and validation. This keeps the catalog stable for agents
    while allowing operation-specific parameter semantics.
    """

    type: str
    # TypedDict cannot express "default type follows the string in `type`";
    # runtime coercion/validation is handled by operation implementations.
    default: str | int | float | bool | None
    required: bool


@dataclass(slots=True)
class OperationDef:
    """Discoverable contract for one event-store named operation.

    Catalog entries describe the operation name, human-readable behavior,
    accepted parameter metadata, and response shape. They are consumed by the
    query API for discovery and by the dispatcher for admission.
    """

    name: str
    description: str
    params: dict[str, ParamDef]
    returns: str


OperationCallable = Callable[[dict[str, Any], "EventStore"], Awaitable[dict[str, Any]]]
