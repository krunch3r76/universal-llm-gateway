from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class PersonaAlias:
    """
    User-local persona alias definition.

    Invariants:
    - Persona aliases are ingress conveniences, not catalog/synthetic model IDs.
    - Persona content is local-only and must not be committed to the repo.
    - Alias params are fill-only: they never override explicit user request params.
    """

    alias_id: str
    backing_model: str
    system_prompt: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
