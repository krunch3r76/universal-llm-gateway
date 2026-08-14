"""Qualified scalar vocabulary and per-surface seal gate (default-deny).

Every scoped or projected numeric/boolean on an agent-facing surface must declare
``scope`` and ``authority``. ``authority`` labels are **producer-attested**: the
emit site declares how the value was obtained; ``seal()`` checks sibling presence,
not semantic truth of the label.

``seal`` is the terminal admission gate: undeclared bare scalars raise at build
time instead of publishing an unqualified fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AuthorityClass(StrEnum):
    """How a scalar's value was obtained — shared by wire scalars and closeout surfaces.

    Labels are **producer-attested** (emit-site declaration). ``seal()`` verifies
    qualifier siblings are present; it does not verify the authority claim is true.
    """

    OBSERVED = "observed"
    RECORDED = "recorded"
    DERIVED = "derived"
    ASSERTED = "asserted"
    MAX_OF = "max(recorded, observed)"
    LEDGER_ATTESTED = "ledger_attested"
    SELF_REPORTED = "self_reported"


class AbsenceSemantics(StrEnum):
    """Whether absence of manifest entries means zero or unknown."""

    ABSENCE_ZERO = "absence=zero"
    ABSENCE_UNKNOWN = "absence=unknown"


@dataclass(frozen=True, slots=True)
class QualifiedScalar:
    """A scalar that carries scope and authority alongside its value.

    Absence law: ``value is None`` means *unobserved*. ``0`` and ``False`` always mean
    *observed-empty* and never *unknown* — callers with no observation must pass ``None``.
    """

    value: int | float | bool | None
    scope: str
    authority: AuthorityClass

    def __post_init__(self) -> None:
        if self.value is not None and isinstance(self.value, bool):
            return
        if self.value is not None and not isinstance(self.value, (int, float, bool)):
            raise TypeError(
                f"QualifiedScalar.value must be int, float, bool, or None; got {type(self.value)!r}"
            )

    def emit(self, name: str) -> dict[str, Any]:
        """Wire rendering: flat sibling keys ``name``, ``name_scope``, ``name_authority``."""
        return {
            name: self.value,
            f"{name}_scope": self.scope,
            f"{name}_authority": self.authority.value,
        }

    def render(self, name: str) -> str:
        """Prose rendering — single formatter; callers must not compose scalar strings."""
        if self.value is None:
            return f"{name}: unobserved"
        return f"{name} ({self.scope}, {self.authority.value}): {self.value}"


@dataclass
class SurfaceDecl:
    """Declares which bare scalars on a surface are intentionally unqualified."""

    surface: str
    _plain: dict[str, str] = field(default_factory=dict)

    def plain(self, name: str, *, reason: str) -> None:
        """Register a bare scalar exempt from qualifier siblings (default-deny exception)."""
        self._plain[name] = reason


class UnqualifiedScalarError(ValueError):
    """Raised when a builder payload contains an undeclared bare numeric or boolean."""


def seal(payload: dict[str, Any], decl: SurfaceDecl) -> dict[str, Any]:
    """Terminal gate: walk *payload* and refuse undeclared bare scalars.

    Every numeric/boolean leaf must either carry ``{key}_scope`` and ``{key}_authority``
    siblings (from :meth:`QualifiedScalar.emit`) or be registered via :meth:`SurfaceDecl.plain`.
    """
    _walk(payload, decl, path="$")
    return payload


def _walk(obj: Any, decl: SurfaceDecl, *, path: str) -> None:
    if isinstance(obj, dict):
        _walk_dict(obj, decl, path=path)
        return
    if isinstance(obj, list):
        for idx, item in enumerate(obj):
            _walk(item, decl, path=f"{path}[{idx}]")
        return


def _walk_dict(d: dict[str, Any], decl: SurfaceDecl, *, path: str) -> None:
    for key, value in d.items():
        child_path = f"{path}.{key}"
        if _is_bare_scalar(value):
            if key in decl._plain:
                continue
            if f"{key}_authority" in d and f"{key}_scope" in d:
                continue
            raise UnqualifiedScalarError(
                f"surface {decl.surface!r}: undeclared bare scalar at {child_path}"
            )
        _walk(value, decl, path=child_path)


def _is_bare_scalar(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    return isinstance(value, (int, float))


# Publication builders (F2 recon + terra census amendments) — records seal installation, does not enforce.
PUBLICATION_BUILDER_CENSUS: dict[str, str] = {
    "sdk_dispatch_gate_stats": "pending",
    "concurrency_stats": "pending",
    "execution_store.active_work_snapshot": "sealed",
    "ImplementCloseout.model_dump": "sealed",
    "lane_a_checkpoint prose injectors": "pending",
    "PropagationRow._apply_defaults": "pending",
    "mcp_drain.active_work_snapshot": "pending",
    "giw.routes.integrate.get_active_work": "pending",
    "giw.routes.cursor_sdk.cursor_concurrency_stats": "pending",
}
