"""source_ref grammar parser and canonicalizer."""

from __future__ import annotations

import re
from dataclasses import dataclass

from implement_admission.spec import SourceKind

_PHASE_SHORTHAND = re.compile(
    r"^plan:(?P<slug>[^/]+)/phase-(?P<num>\d+)$",
    re.IGNORECASE,
)
_AGENT_BUS = re.compile(
    r"^agent-bus:(?P<thread>\d+)(?:#turn-(?P<turn>\d+))?$",
    re.IGNORECASE,
)
_ENTITY_REF = re.compile(
    r"^(?P<kind>todo|plan|plan_phase):(?P<rest>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SourceRef:
    external_ref: str
    canonical_ref: str
    parent_ref: str | None
    selector: str | None
    source_kind: str
    turn: int | None = None


class SourceRefError(Exception):
    """Typed resolution failure carrying code, source_ref, and rule."""

    def __init__(
        self, *, code: str, source_ref: str, rule: str, message: str | None = None
    ) -> None:
        self.code = code
        self.source_ref = source_ref
        self.rule = rule
        super().__init__(message or f"{code}: {source_ref} ({rule})")


def parse_source_ref(raw: str) -> SourceRef:
    """Parse external source_ref into canonical form per §2 grammar table."""
    text = raw.strip()
    if not text:
        raise SourceRefError(
            code="source_ref_unparseable",
            source_ref=raw,
            rule="non-empty string required",
        )

    phase_match = _PHASE_SHORTHAND.match(text)
    if phase_match:
        slug = phase_match.group("slug")
        num = phase_match.group("num")
        selector = f"phase-{num}"
        plan_ref = f"plan:{slug}"
        canonical = f"plan_phase:{slug}/{selector}"
        return SourceRef(
            external_ref=text,
            canonical_ref=canonical,
            parent_ref=plan_ref,
            selector=selector,
            source_kind=SourceKind.PLAN_PHASE.value,
        )

    bus_match = _AGENT_BUS.match(text)
    if bus_match:
        thread = bus_match.group("thread")
        turn_raw = bus_match.group("turn")
        turn = int(turn_raw) if turn_raw else None
        canonical = f"agent-bus:{thread}"
        if turn is not None:
            canonical = f"{canonical}#turn-{turn}"
        return SourceRef(
            external_ref=text,
            canonical_ref=canonical,
            parent_ref=None,
            selector=None,
            source_kind=SourceKind.AGENT_BUS.value,
            turn=turn,
        )

    if text.lower().startswith("packet:"):
        payload = text.split(":", 1)[1]
        if not payload:
            raise SourceRefError(
                code="source_ref_unparseable",
                source_ref=raw,
                rule="packet: requires path or URI",
            )
        return SourceRef(
            external_ref=text,
            canonical_ref=text,
            parent_ref=None,
            selector=None,
            source_kind=SourceKind.PACKET.value,
        )

    entity_match = _ENTITY_REF.match(text)
    if entity_match:
        kind = entity_match.group("kind").lower().replace("_", "-")
        if kind == "plan-phase":
            kind = SourceKind.PLAN_PHASE.value
        elif kind == "plan":
            kind = SourceKind.PLAN.value
        elif kind == "todo":
            kind = SourceKind.TODO.value
        else:
            kind = entity_match.group("kind").lower()
        rest = entity_match.group("rest")
        canonical = (
            f"{kind}:{rest}"
            if kind != SourceKind.PLAN_PHASE.value
            else f"plan_phase:{rest}"
        )
        parent: str | None = None
        selector: str | None = None
        if kind == SourceKind.PLAN_PHASE.value:
            if "/" in rest:
                slug, phase_sel = rest.split("/", 1)
                parent = f"plan:{slug}"
                selector = phase_sel
            canonical = f"plan_phase:{rest}"
        return SourceRef(
            external_ref=text,
            canonical_ref=canonical,
            parent_ref=parent,
            selector=selector,
            source_kind=kind if isinstance(kind, str) else kind.value,
        )

    raise SourceRefError(
        code="source_ref_unparseable",
        source_ref=raw,
        rule="must match todo:|plan:|plan_phase:|plan:{slug}/phase-N|agent-bus:|packet:",
    )
