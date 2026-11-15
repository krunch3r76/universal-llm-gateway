"""Resolve a named claude.ai Project before CDP compose submit."""

from __future__ import annotations

from claude_bundles.project_chrome import (
    DEFAULT_ORG_ID,
    ClaudeProjectNotFoundError,
    resolve_project_uuid_by_name,
)
from claude_bundles.skills_ui_panel import connect_cdp

from cdp_ask.claude_project_defaults import DEFAULT_CLAUDE_PROJECT_NAME
from cdp_ask.models import SubmitProjectAskRequest

__all__ = [
    "DEFAULT_CLAUDE_PROJECT_NAME",
    "ClaudeProjectSelectError",
    "apply_project_select",
    "project_select_needed",
]


class ClaudeProjectSelectError(ClaudeProjectNotFoundError):
    """cdp_ask wrapper — named Project could not be bound before submit."""


def project_select_needed(req: SubmitProjectAskRequest) -> bool:
    """Return whether this submit must resolve ``claude_project_name`` to a uuid."""
    if (req.claude_surface or "cowork").strip().lower() == "code":
        return False
    if (req.project_uuid or "").strip():
        return False
    return True


async def apply_project_select(
    req: SubmitProjectAskRequest,
    *,
    cdp_url: str,
    org_id: str = DEFAULT_ORG_ID,
) -> SubmitProjectAskRequest:
    """Select the named Project on the CDP lane and pin ``project_uuid`` on *req*."""
    if not project_select_needed(req):
        return req
    raw = req.claude_project_name
    if raw is None:
        name = DEFAULT_CLAUDE_PROJECT_NAME
    else:
        name = raw.strip()
        if not name:
            raise ClaudeProjectSelectError("claude project name is required")
    pw = None
    try:
        pw, _browser, _context, page = await connect_cdp(cdp_url)
    except Exception as exc:
        raise ClaudeProjectSelectError(
            f"cdp connect for project select failed: {exc}"
        ) from exc
    try:
        try:
            project_uuid = await resolve_project_uuid_by_name(
                page, name, org_id=org_id
            )
        except ClaudeProjectNotFoundError as exc:
            raise ClaudeProjectSelectError(str(exc)) from exc
        except RuntimeError as exc:
            raise ClaudeProjectSelectError(str(exc)) from exc
    finally:
        if pw is not None:
            await pw.stop()
    return req.model_copy(
        update={
            "project_uuid": project_uuid,
            "no_project_uuid": False,
            "claude_project_name": name,
        }
    )
