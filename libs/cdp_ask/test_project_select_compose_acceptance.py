"""Unmocked acceptance: project_select + project compose birth → skills-attach surface.

Run on Jupiter (CDP):
  ULG_DISPATCH_INTEGRATION=1 pytest libs/cdp_ask/test_project_select_compose_acceptance.py -q --tb=short
"""

from __future__ import annotations

import asyncio
import os

import pytest

from cdp_ask.claude_project_defaults import DEFAULT_CLAUDE_PROJECT_NAME
from cdp_ask.models import SubmitProjectAskRequest
from cdp_ask.project_select import apply_project_select

pytestmark = pytest.mark.integration

_INTEGRATION = os.environ.get("ULG_DISPATCH_INTEGRATION") == "1"


@pytest.fixture(autouse=True)
def _require_live_cdp() -> None:
    if not _INTEGRATION:
        pytest.skip(
            "Set ULG_DISPATCH_INTEGRATION=1 for unmocked project_select compose acceptance"
        )


async def _project_select_compose_surface_probe() -> None:
    from claude_bundles.chat_session_hygiene import goto_fresh_compose
    from claude_bundles.composer_session_skills import attest_compose_surface_for_skills
    from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp

    req = SubmitProjectAskRequest(
        prompt_text="compose surface acceptance probe",
        converse=True,
        no_project_uuid=True,
        claude_project_name=DEFAULT_CLAUDE_PROJECT_NAME,
        model="opus-5",
    )
    selected = await apply_project_select(req, cdp_url=DEFAULT_CDP_URL)
    assert (selected.project_uuid or "").strip()

    pw, _browser, _ctx, page = await connect_cdp(DEFAULT_CDP_URL)
    try:
        await goto_fresh_compose(page, project_uuid=selected.project_uuid)
        landed = page.url or ""
        assert f"/cowork/project/{selected.project_uuid}" in landed
        await attest_compose_surface_for_skills(page)
    finally:
        await pw.stop()


def test_project_select_birth_lands_on_skills_compose_surface() -> None:
    """Named project select → goto_fresh_compose must pass skills compose attest."""
    asyncio.run(_project_select_compose_surface_probe())