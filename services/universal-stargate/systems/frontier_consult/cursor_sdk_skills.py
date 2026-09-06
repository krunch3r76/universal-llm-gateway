"""Admit-time ``skills=`` resolution for the cursor-sdk generate branch.

Who calls: :func:`prepare_cursor_sdk_generate`, once per
``team_dispatch(op=generate, seat=cursor-sdk)``.

``seat=cursor-sdk`` forks out of the route before ``build_dispatch_body`` runs
(``route.py`` cursor-sdk generate admission), so the ``partition_skill_channels``
machinery on the API-role path never sees these dispatches — which is why
``skills=`` used to be parsed into ``TeamDispatchGenerateBody`` and then silently
dropped. This module is the cursor-sdk channel for the same wire param.

Two deliberate divergences from ``partition_skill_channels``:

* **No scope-default merge.** ``resolve_effective_skills`` folds
  ``scope_default_skill_ids`` into the caller list because a provider dispatch has
  no ambient guidance layer. A cursor-sdk seat does: the ecosystem plugin census,
  the ``alwaysApply`` rule set, the seat overlay, and the fixed judgment preambles
  are already its scope-default channel. Merging them again would stage duplicate
  bodies of guidance the seat has resident.
* **Channel is the filesystem, not a container.** ``partition_skill_channels``
  routes Layer B by ``skills_mount_backend``, whose only mounting value is
  ``openai_container`` — a base64 zip for a provider with no filesystem. Cursor
  discovers skills by reading directories, so Layer B here is "resolvable to a
  readable ``SKILL.md``", reported on the same ``dispatch.skills.channel.resolved``
  signal.

Unresolvable slugs fail the admit with 422. That is the point of the fix: the
observed failure mode was a dispatch that succeeded while ignoring its skills, so
a slug with no body is now a refusal that names the slug.
"""

from __future__ import annotations

from typing import Any

from agent_seat.skills_merge import normalize_caller_skill_ids
from skills_mount.cursor_fs import classify_cursor_skills
from skills_mount.resolve import default_workspaces_root

from .cursor_sdk_generate_signals import publish_frontier_event
from .events import DispatchSkillsChannelResolved


def resolve_cursor_sdk_skills(
    skills: list[str] | None,
    *,
    request_id: str,
    role: str | None,
    resolved_model: str,
    event_publisher: Any | None = None,
) -> tuple[str, ...]:
    """Return canonical slugs whose bodies the worker can stage.

    Args:
        skills: Caller ``skills=`` list from the dispatch body.
        request_id: Admit request id, for the error and event payloads.
        role: Seat name (``cursor-sdk``).
        resolved_model: Effective ``cursor/*`` model id.
        event_publisher: Optional publisher; defaults to the module-level emitter.

    Returns:
        Canonical slugs in caller order, deduped. Empty when no skills were asked
        for.

    Raises:
        FrontierEndpointError: 422 ``skills_cursor_unresolvable`` when any requested
            slug has no Cursor-discoverable ``SKILL.md``.
    """
    requested = normalize_caller_skill_ids(skills)
    if not requested:
        return ()

    repo_root = default_workspaces_root()
    resolution = classify_cursor_skills(requested, repo_root=repo_root)

    if resolution.unresolved:
        from .admission import FrontierEndpointError

        raise FrontierEndpointError(
            request_id=request_id,
            field="skills",
            reason=(
                "cursor-sdk skills= requires a Cursor-discoverable SKILL.md for "
                "every slug: "
                + "; ".join(f"{slug}: {why}" for slug, why in resolution.unresolved)
            ),
            status_code=422,
            code="skills_cursor_unresolvable",
            details={
                "model": resolved_model,
                "skills": list(resolution.unresolved_slugs),
                "repo_root": str(repo_root),
                "reason_code": "skills_cursor_unresolvable",
            },
        )

    rows = [
        {
            "requested_id": sot.requested_id,
            "canonical_id": f"agent_skill:{sot.canonical_slug}",
            "origin": "caller",
            "channel": "layer_b",
            "disposition": "delivered",
            "sot_layer": sot.layer,
        }
        for sot in resolution.resolved
    ]
    event = DispatchSkillsChannelResolved(
        request_id=request_id,
        role=role,
        model=resolved_model,
        skills=rows,
    )
    if event_publisher is not None:
        event_publisher(event)
    else:
        publish_frontier_event(event)
    return tuple(sot.canonical_slug for sot in resolution.resolved)
