"""Opt-in promotion of session objectives to recon-pending todos at session close.

Called from ``ops_session_close._op_session_close`` when ``promote_todos`` is supplied.
Uses the rich-seed-floor helper from Phase 3 (``_recon_seed.seed_recon_todo``).
"""

from __future__ import annotations

from typing import Any

from ._shared import record


def promote_session_objectives(
    promote_todos: list[dict[str, Any]] | None,
    *,
    session_id: str,
    agent: str,
) -> list[dict[str, Any]]:
    """Seed recon-pending todos from named session objectives at close.

    Opt-in: only the explicitly-passed objectives are promoted (no auto-scan of
    open_items). Each todo is created at the rich-seed floor (Phase 3 helper) with
    the session transcript as provenance context. Returns per-objective seed results.
    """
    if not promote_todos:
        return []
    from ._recon_seed import seed_recon_todo

    out: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for index, spec in enumerate(promote_todos):
        # Pre-commit validation with explicit diagnostics. A malformed spec
        # (non-dict, or empty slug/name — e.g. a "Name" vs "name" casing typo)
        # must NOT silently `continue`: a success-reporting session close would
        # then evaporate the objective with no signal. Record the drop instead so
        # it surfaces in the result and emits telemetry
        # (decision:recon-lifecycle-phase-review §P4).
        if not isinstance(spec, dict):
            dropped.append({"index": index, "reason": "spec_not_a_mapping"})
            continue
        slug = str(spec.get("slug") or "").strip()
        name = str(spec.get("name") or "").strip()
        if not slug or not name:
            missing = [
                k for k in ("slug", "name") if not str(spec.get(k) or "").strip()
            ]
            dropped.append(
                {
                    "index": index,
                    "reason": "missing_required_field",
                    "missing": missing,
                    "keys_present": sorted(str(k) for k in spec),
                }
            )
            continue
        todo_id = slug if slug.startswith("todo:") else f"todo:{slug}"
        bare = todo_id.removeprefix("todo:")
        raw_skills = spec.get("required_skills")
        required_skills = (
            [str(s) for s in raw_skills] if isinstance(raw_skills, list) else []
        )
        source_uri = str(spec.get("source_uri") or f"tasks/specs/{bare}.md")
        result = seed_recon_todo(
            todo_id=todo_id,
            name=name,
            source_uri=source_uri,
            required_skills=required_skills,
            seed_ack=(
                f"promoted from session objective ({session_id}); "
                "recon pending (density_triage=recon_pending)"
            ),
            context_target_id=f"transcript:{session_id}",
            extra_attrs={"promoted_from_session": session_id},
            agent=agent,
            session_id=session_id,
        )
        # result is None → dedup (todo already exists), neither promoted nor
        # dropped. A {"error": ...} seed failure is a drop, NOT a promotion:
        # appending it to `out` would over-count cortex.session.objective.promoted
        # (thread 3478 F2). Only {"todo_created": ...} counts as a promotion.
        if isinstance(result, dict) and "todo_created" in result:
            out.append(result)
        elif isinstance(result, dict) and "error" in result:
            dropped.append(
                {"index": index, "reason": "seed_failed", "error": result["error"]}
            )
    if out:
        record(
            "cortex.session.objective.promoted",
            session_id=session_id,
            agent=agent,
            promoted_count=len(out),
        )
    if dropped:
        record(
            "cortex.session.objective.dropped",
            session_id=session_id,
            agent=agent,
            dropped_count=len(dropped),
        )
        out.extend({"dropped": d} for d in dropped)
    return out


__all__ = ["promote_session_objectives"]
