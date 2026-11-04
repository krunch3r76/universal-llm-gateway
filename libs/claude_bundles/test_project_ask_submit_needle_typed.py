"""Submit needle must match composer insert_text, not the sealed prompt (a:42138)."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.composer_submit import composer_holds_needle, verification_marker

pytestmark = pytest.mark.offline

# Marker (>40 chars) is stripped before insert_text; full prompt stays short so
# verification_marker(sealed text) reads the header slice, not the typed body.
_LONG_MARKER = "<!--cdp-required-skills:orchestrator-workflow-->\n"
_INLINE = (
    '<skills_inline><skill slug="orchestrator-workflow" '
    'surface_class="cursor_only">pin</skill></skills_inline>\n\n'
)
_BODY = "Sealed inline-only body for submit needle alignment.\n"


def _page_for_insert() -> AsyncMock:
    page = AsyncMock()
    page.url = "https://claude.ai/new"
    page.keyboard = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    return page


@pytest.mark.asyncio
async def test_marked_inline_only_submit_needle_matches_insert_text() -> None:
    """Else branch (not induction+marked): draft_text must be what insert_text typed."""
    from claude_bundles.project_ask import send_prompt

    page = _page_for_insert()
    composer = AsyncMock()
    composer.click = AsyncMock()
    submit_drafts: list[str] = []

    async def _capture_submit(*_args, **kwargs):
        submit_drafts.append(str(kwargs.get("draft_text") or ""))

    text = _LONG_MARKER + _INLINE + _BODY

    with ExitStack() as stack:
        stack.enter_context(
            patch("claude_bundles.composer_session_skills.require_compose_surface")
        )
        stack.enter_context(
            patch(
                "claude_bundles.project_ask.find_composer",
                new=AsyncMock(return_value=composer),
            )
        )
        stack.enter_context(
            patch(
                "claude_bundles.composer_submit.clear_composer_verified",
                new=AsyncMock(),
            )
        )
        stack.enter_context(
            patch(
                "claude_bundles.project_ask._submit_composer_draft",
                side_effect=_capture_submit,
            )
        )
        stack.enter_context(
            patch(
                "claude_bundles.skill_induction_panel.wait_for_induction_panel",
                new=AsyncMock(),
            )
        )
        stack.enter_context(
            patch(
                "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
                new=AsyncMock(),
            )
        )
        await send_prompt(page, text)

    assert submit_drafts, "expected one submit on marked inline-only path"
    draft_text = submit_drafts[-1]
    insert = page.keyboard.insert_text
    assert insert.await_count >= 1, "expected body insert_text on sealed inline path"
    typed = str(insert.await_args_list[-1].args[0])

    needle = verification_marker(draft_text)
    assert needle, "draft_text must yield a non-empty verification needle"
    assert draft_text == typed
    assert composer_holds_needle({"text": typed}, needle), (
        "submit needle must come from typed composer payload, not sealed prompt header"
    )
