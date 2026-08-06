"""Claim register types — observed vs derived for claim-bearing surfaces.

Row 29 standing invariant: the fleet must distinguish what it observed from
what it inferred. ``ClaimRegister`` is that discriminator. It is intentionally
not Cortex ``DerivationType`` (assertion substrate only); correspondence may
be documented later — identity is rejected.

Fail-closed at construction: ``Claimed`` accepts only ``observed``|``derived``.
Post-time untyped claims use ``CLAIM_REGISTER_UNKNOWN`` via wire degrade — never
via this constructor — so a missing register cannot silently become a typed
claim.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypeVar

ClaimRegister = Literal["observed", "derived"]

CLAIM_REGISTER_UNKNOWN = "unknown"
"""Post-time degrade token when a claim-bearing key arrives without a register.

Not a valid ``Claimed.register`` — construction refuses it. Terminal posts may
stamp this on the wire so the closeout still lands (see ``wire.normalize_…``).
"""

_VALID_REGISTERS = frozenset({"observed", "derived"})

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Claimed[T]:
    """Value carrier tagged with claim register (observed vs derived).

    Construction is fail-closed: ``register`` must be ``observed`` or ``derived``.
    Use :func:`claimed_observed` / :func:`claimed_derived` at emit sites.
    """

    register: ClaimRegister
    value: T
    basis: str | None = None

    def __post_init__(self) -> None:
        if self.register not in _VALID_REGISTERS:
            raise ValueError(
                "Claimed.register must be 'observed' or 'derived' at construction; "
                f"got {self.register!r}. Post-time untyped claims stamp "
                f"{CLAIM_REGISTER_UNKNOWN!r} via wire normalize — do not construct "
                "Claimed with that token."
            )

    def to_wire(self) -> dict[str, Any]:
        """Serialize for JSON terminal payloads (register + value [+ basis])."""
        out: dict[str, Any] = {"register": self.register, "value": self.value}
        if self.basis is not None:
            out["basis"] = self.basis
        return out


def claimed_observed(value: T, *, basis: str | None = None) -> Claimed[T]:
    """Tag *value* as observed (gate identity, substrate quote, tool payload)."""
    return Claimed(register="observed", value=value, basis=basis)


def claimed_derived(value: T, *, basis: str | None = None) -> Claimed[T]:
    """Tag *value* as derived counsel such as fix_hint, implication, or next-step advice."""
    return Claimed(register="derived", value=value, basis=basis)
