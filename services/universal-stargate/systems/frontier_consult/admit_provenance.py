"""Admit provenance flags for team_dispatch generate / to_thread (friction:39669).

Records missing caller seat, lane/thread, or work_key without refusing the admit.
Downstream bus posts must not synthesize ``from=dispatch`` when the caller
seat is blank.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PROVENANCE_MISSING_PREFIX = "PROVENANCE_MISSING:"
# Non-dispatch ``from`` when caller_agent is blank but the admit is flagged.
MISSING_CALLER_FROM_AGENT = "frontier-admit"


def _strip(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def missing_admit_provenance(
    caller_seat: str | None,
    lane_or_thread: str | None,
    work_key: str | None,
) -> tuple[str, ...]:
    """Return sorted names of blank provenance fields on an admit.

    Blank means ``None`` or whitespace-only. Used once at ``team_dispatch`` for
    ``op=generate`` and ``op=to_thread`` before branching to substrate handlers.
    """
    missing: list[str] = []
    for name, raw in (
        ("caller_seat", caller_seat),
        ("lane_or_thread", lane_or_thread),
        ("work_key", work_key),
    ):
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            missing.append(name)
    return tuple(sorted(missing))


def provenance_missing_marker(missing: tuple[str, ...]) -> str | None:
    """Body line ``PROVENANCE_MISSING:…`` or None when the set is empty."""
    if not missing:
        return None
    return f"{PROVENANCE_MISSING_PREFIX}{','.join(missing)}"


def append_provenance_marker(body: str, missing: tuple[str, ...]) -> str:
    """Append a ``PROVENANCE_MISSING`` line when any field is blank."""
    marker = provenance_missing_marker(missing)
    if marker is None:
        return body
    if body.endswith("\n"):
        return f"{body}{marker}"
    return f"{body}\n{marker}"


def admit_turn_from_agent(
    caller_agent: str | None,
    *,
    provenance_missing: tuple[str, ...],
) -> str:
    """Resolve agent-bus ``from`` / ``from_agent`` for an admit coordination turn.

    Full provenance keeps the historical ``caller or dispatch`` fallback. Flagged
    admits never use the literal ``dispatch`` when the caller seat is blank.
    """
    seat = _strip(caller_agent)
    if not provenance_missing:
        return seat or "dispatch"
    if seat:
        return seat
    return MISSING_CALLER_FROM_AGENT


@dataclass(frozen=True, slots=True)
class AdmitProvenanceContext:
    """Computed once at ``team_dispatch`` for generate / to_thread admits."""

    missing: tuple[str, ...]
    caller_seat: str | None
    lane_or_thread: str | None
    work_key: str | None
    coord_thread: str | None
    root_lane: bool
    request_id: str
    # Cell, not a bool field: the context is frozen and the sqlite write
    # happens after the route has already captured this object for the 202.
    _record_failed: list[bool] = field(
        default_factory=lambda: [False], repr=False, compare=False
    )

    @property
    def missing_list(self) -> list[str]:
        return list(self.missing)

    @property
    def provenance_record_failed(self) -> bool:
        return self._record_failed[0]

    def note_provenance_record_failed(self) -> None:
        """Record that the admission-context write failed. Admit still proceeds."""
        self._record_failed[0] = True

    def enrich_admit_response(self, result: dict[str, Any]) -> dict[str, Any]:
        if not self.missing and not self.provenance_record_failed:
            return result
        out = dict(result)
        if self.missing:
            out["provenance_missing"] = self.missing_list
        if self.provenance_record_failed:
            out["provenance_record_failed"] = True
        return out


def build_team_dispatch_admit_provenance(
    body: Any,
    *,
    request_id: str,
) -> AdmitProvenanceContext | None:
    """Build provenance context for ``team_dispatch`` generate / to_thread only."""
    op = getattr(body, "op", None)
    if op not in ("generate", "to_thread"):
        return None
    caller_seat = _strip(getattr(body, "caller_agent", None))
    dispatch_thread_id = _strip(getattr(body, "dispatch_thread_id", None))
    target_thread = _strip(getattr(body, "thread", None))
    lane_or_thread = dispatch_thread_id or target_thread
    work_key = _strip(getattr(body, "work_key", None))
    missing = missing_admit_provenance(caller_seat, lane_or_thread, work_key)
    coord_thread = dispatch_thread_id or target_thread
    nest_under = _strip(getattr(body, "nest_under", None))
    resume_of = _strip(getattr(body, "resume_of", None))
    root_lane = bool(coord_thread) and not nest_under and not resume_of
    return AdmitProvenanceContext(
        missing=missing,
        caller_seat=caller_seat,
        lane_or_thread=lane_or_thread,
        work_key=work_key,
        coord_thread=coord_thread,
        root_lane=root_lane,
        request_id=request_id,
    )


def emit_admit_provenance_missing_event(
    ctx: AdmitProvenanceContext,
    *,
    execution_id: str | None = None,
    provenance_record_failed: bool = False,
) -> None:
    """Publish ``dispatch.admit.provenance_missing``; failures must not fail admit.

    ``provenance_record_failed`` is set when the admission-context store write
    raised. The publish itself is swallowed so a broken event bus cannot refuse
    the admit.
    """
    if not ctx.missing and not provenance_record_failed:
        return
    try:
        from .cursor_sdk_generate_signals import publish_frontier_event
        from .events import DispatchAdmitProvenanceMissing

        publish_frontier_event(
            DispatchAdmitProvenanceMissing(
                missing=ctx.missing_list,
                execution_id=execution_id or ctx.request_id,
                coord_thread=ctx.coord_thread,
                root_lane=ctx.root_lane,
                request_id=ctx.request_id,
                provenance_record_failed=provenance_record_failed,
            )
        )
    except Exception:
        return


def finalize_team_dispatch_admit_response(
    result: dict[str, Any] | Any,
    ctx: AdmitProvenanceContext | None,
) -> Any:
    """Attach provenance flags to successful 202 JSON bodies. Never refuses."""
    if ctx is None:
        return result
    if not ctx.missing and not ctx.provenance_record_failed:
        return result
    if isinstance(result, dict):
        return ctx.enrich_admit_response(result)
    return result
