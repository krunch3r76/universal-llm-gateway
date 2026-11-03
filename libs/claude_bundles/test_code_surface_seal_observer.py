"""Real seal observer tests for claude_surface=code (friction a:41730)."""

from __future__ import annotations

import re
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest

from claude_bundles.composer_submit import _MARKER_IN_COMMITTED_JS
from claude_bundles.cowork_skill_delivery import SkillReceiptUnverifiedError

pytestmark = pytest.mark.offline

_CASE_A = (
    "<!--cdp-required-skills:reasoning-posture-->\n"
    "# Review packet\n"
    "Quoted:\n"
    "<skills_inline>\n"
    '<skill slug="reasoning-posture" surface_class="shared_sync">\n'
    "quoted text\n"
    "</skill>\n"
    "</skills_inline>\n"
    "End.\n"
)

_CASE_B = "<!--cdp-required-skills:reasoning-posture-->\n# Work body\n"


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


class _FakeCommittedPage:
    def __init__(self) -> None:
        self.committed: list[str] = []
        self.evaluate_calls = 0
        self.keyboard = AsyncMock()

    async def evaluate(self, script: str, needle: str) -> bool:
        self.evaluate_calls += 1
        if script != _MARKER_IN_COMMITTED_JS:
            return False
        norm_needle = _norm_ws(needle)
        for block in self.committed:
            if norm_needle and norm_needle in _norm_ws(block):
                return True
        return False

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


def _drop_skill_seal_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.startswith("skill-seal:")
    )


def _commit_as_typed(text: str) -> str:
    return text


def _code_observer_harness(
    *,
    commit_transform=_commit_as_typed,
    emit_capture: list[dict] | None = None,
):
    page = _FakeCommittedPage()
    composer = AsyncMock()
    submit_calls: list[str] = []

    async def _submit(*_args, **kwargs):
        draft = kwargs.get("draft_text") or ""
        submit_calls.append(draft)
        page.committed.append(commit_transform(draft))

    def _emit(**payload):
        if emit_capture is not None:
            emit_capture.append(dict(payload))

    patches = (
        patch("claude_bundles.composer_session_skills.require_compose_surface"),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=composer),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.composer_skill_observe.attach_skills_verified",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "claude_bundles.project_ask._submit_composer_draft",
            side_effect=_submit,
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.emit_skill_delivery_attested",
            side_effect=_emit,
        ),
        patch.object(page.keyboard, "insert_text", new=AsyncMock()),
    )
    return page, submit_calls, patches


@pytest.mark.asyncio
async def test_await_committed_skill_seals_present_absent_and_nonce() -> None:
    from claude_bundles.cowork_skill_delivery import (
        await_committed_skill_seals,
        render_code_surface_inline_block,
        skill_inline_seal_line,
    )

    block, _, seals = render_code_surface_inline_block(["reasoning-posture"])
    page = _FakeCommittedPage()
    page.committed.append(block)
    got = await await_committed_skill_seals(page, seals)
    assert got == ["reasoning-posture"]

    page2 = _FakeCommittedPage()
    got2 = await await_committed_skill_seals(page2, seals)
    assert got2 == []

    slug, good_line = seals[0]
    bad_line = skill_inline_seal_line(slug, "deadbeef0000")
    page3 = _FakeCommittedPage()
    page3.committed.append(block.replace(good_line, bad_line))
    got3 = await await_committed_skill_seals(page3, seals)
    assert got3 == []

    block2, _, seals2 = render_code_surface_inline_block(
        ["reasoning-posture", "consult-posture"]
    )
    page4 = _FakeCommittedPage()
    page4.committed.append(block2)
    only_first = [seals2[0]]
    got4 = await await_committed_skill_seals(page4, only_first)
    assert got4 == ["reasoning-posture"]


@pytest.mark.asyncio
async def test_code_send_quoted_inline_without_seal_fails() -> None:
    from claude_bundles.project_ask import send_prompt

    emit: list[dict] = []
    page, _submit, patches = _code_observer_harness(
        commit_transform=_drop_skill_seal_lines,
        emit_capture=emit,
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        with pytest.raises(
            SkillReceiptUnverifiedError, match=r"undelivered=\['reasoning-posture'\]"
        ):
            await send_prompt(page, _CASE_A, claude_surface="code")
    assert emit
    last = emit[-1]
    assert last["ok"] is False
    assert last["inline_observed"] == []
    assert last["claude_surface"] == "code"
    assert last["rows"] == [
        {"slug": "reasoning-posture", "delivered_via": "undelivered"}
    ]
    assert page.evaluate_calls >= 1


@pytest.mark.asyncio
async def test_code_send_quoted_inline_empty_commit_fails() -> None:
    from claude_bundles.project_ask import send_prompt

    emit: list[dict] = []

    def _commit_nothing(_text: str) -> str:
        return ""

    page, _submit, patches = _code_observer_harness(
        commit_transform=_commit_nothing,
        emit_capture=emit,
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        with pytest.raises(SkillReceiptUnverifiedError, match="undelivered"):
            await send_prompt(page, _CASE_A, claude_surface="code")
    assert emit[-1]["ok"] is False


@pytest.mark.asyncio
async def test_code_send_simple_marked_body_seal_observed() -> None:
    from claude_bundles.project_ask import send_prompt

    emit: list[dict] = []
    page, _submit, patches = _code_observer_harness(emit_capture=emit)
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        await send_prompt(page, _CASE_B, claude_surface="code")
    assert emit
    last = emit[-1]
    assert last["ok"] is True
    assert last["inline_observed"] == ["reasoning-posture"]
    assert last["claude_surface"] == "code"
    assert last["rows"] == [{"slug": "reasoning-posture", "delivered_via": "inline"}]


@pytest.mark.asyncio
async def test_code_send_partial_seal_fails() -> None:
    from claude_bundles.project_ask import send_prompt

    text = (
        "<!--cdp-required-skills:reasoning-posture,consult-posture-->\n"
        "# Work\n"
        '<skills_inline><skill slug="consult-posture" surface_class="shared_sync">'
        "x</skill></skills_inline>\n"
    )

    def _drop_consult_only(draft: str) -> str:
        return "\n".join(
            line
            for line in draft.splitlines()
            if not line.startswith("skill-seal: consult-posture")
        )

    emit: list[dict] = []
    page, _submit, patches = _code_observer_harness(
        commit_transform=_drop_consult_only,
        emit_capture=emit,
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        with pytest.raises(
            SkillReceiptUnverifiedError, match=r"undelivered=\['consult-posture'\]"
        ):
            await send_prompt(page, text, claude_surface="code")
    assert emit[-1]["ok"] is False


@pytest.mark.asyncio
async def test_code_send_stale_seal_on_second_dispatch() -> None:
    from claude_bundles.project_ask import send_prompt

    emit: list[dict] = []
    mode = {"drop_seals": False}

    def _transform(text: str) -> str:
        if mode["drop_seals"]:
            return _drop_skill_seal_lines(text)
        return _commit_as_typed(text)

    page, _submit, patches = _code_observer_harness(
        commit_transform=_transform,
        emit_capture=emit,
    )
    ids = dict(
        stargate_execution_id="e" * 32,
        satellite_execution_id="f" * 32,
    )
    with ExitStack() as stack:
        for item in patches:
            stack.enter_context(item)
        await send_prompt(page, _CASE_B, claude_surface="code", **ids)
        page.committed.clear()
        page.evaluate_calls = 0
        mode["drop_seals"] = True
        with pytest.raises(SkillReceiptUnverifiedError):
            await send_prompt(page, _CASE_B, claude_surface="code", **ids)
    assert emit[-1]["ok"] is False
