"""Unit tests for project-ask helpers (no CDP)."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles import project_ask_abort as abort
from claude_bundles.chat_reply_wait import HarvestIncompleteError
from claude_bundles.chat_session_hygiene import _page_score
from claude_bundles.project_ask import (
    archive_harvest,
    finalize_scrape_body,
    project_ask_on_page,
    read_archive_execution_id,
    strip_thinking_prefix,
    submit_control_names,
)
from claude_bundles.project_chrome import project_url

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path):
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9226))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    return root


def _noop_launch(port: int, profile):
    profile.mkdir(parents=True, exist_ok=True)
    return 1


def test_project_url_shape() -> None:
    uuid = "019f6917-2ab2-772c-a1ec-f88434b08e32"
    assert project_url(uuid) == f"https://claude.ai/cowork/project/{uuid}"


def test_page_score_prefers_chat_over_project() -> None:
    assert _page_score("https://claude.ai/chat/abc") > _page_score(
        "https://claude.ai/cowork/project/019f6917-2ab2-772c-a1ec-f88434b08e32"
    )


def test_strip_thinking_prefix() -> None:
    raw = (
        "Thinking about concerns with this request\n"
        "Thinking about concerns with this request\n\n"
        "ASK_HARNESS_OK\nPROJECT=SCC\n"
    )
    cleaned = strip_thinking_prefix(raw)
    assert cleaned.startswith("ASK_HARNESS_OK")
    assert "Thinking about" not in cleaned


def test_finalize_scrape_body_drops_37508_chrome_keeps_command() -> None:
    raw = (
        "Claude responded: VERDICT: Change\n"
        "Used toys integration, loaded tools, loaded a skill\n"
        "\ue027\n"
        "Used toys integration, loaded tools, loaded a skill\n"
        "\n"
        "VERDICT: Change\n"
        'not `$ULG_REPO`: HOME="$(getent passwd "$(id -un)" | cut -d: -f6)" '
        "/mnt/torus/projects/universal-llm-gateway/scripts/cursor/"
        "install-ecosystem-plugin.sh\n"
        "3 minutes ago"
    )
    cleaned = finalize_scrape_body(raw)
    assert "Used toys integration" not in cleaned
    assert "3 minutes ago" not in cleaned
    assert "Claude responded:" not in cleaned
    assert "VERDICT: Change" in cleaned
    assert "$ULG_REPO" in cleaned
    assert "install-ecosystem-plugin.sh" in cleaned


def test_finalize_scrape_body_keeps_code_block_brace_lines() -> None:
    raw = "Here is the patch:\n```python\ndef f(x):\n    return x\n}\n```\n---\ndone\n"
    cleaned = finalize_scrape_body(raw)
    assert "}\n```" in cleaned.replace("\r", "")
    assert "---" in cleaned
    assert "def f(x):" in cleaned


def test_submit_control_names_cowork_before_chat() -> None:
    """Friction 24609 — Cowork Start task precedes Chat Send message."""
    names = submit_control_names()
    assert names[0] == "Start task"
    assert "Send message" in names


def test_archive_harvest_stamps_stargate_execution_id(tmp_path: Path) -> None:
    archive = tmp_path / "harvest.md"
    archive_harvest(
        body="ok",
        url="https://claude.ai/cowork/cse_015Wj9BxzFrBhp6D5jPoQW7D",
        project_uuid="",
        model={"ok": True},
        attested_model="sonnet-5",
        archive_path=str(archive),
        execution_id="sat" + "a" * 29,
        stargate_execution_id="eddc877e-3f63-439a-a115-994b2856200f",
    )
    text = archive.read_text(encoding="utf-8")
    assert "- stargate_execution_id: `eddc877e-3f63-439a-a115-994b2856200f`" in text
    assert "- execution_id: `" + ("sat" + "a" * 29) + "`" in text


def test_archive_harvest_same_execution_growth_rewrites(tmp_path: Path) -> None:
    archive = tmp_path / "harvest.md"
    execution_id = "exec" + "a" * 28
    uri = archive_harvest(
        body="first",
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(archive),
        execution_id=execution_id,
    )
    assert uri.startswith("file://")
    assert read_archive_execution_id(str(archive)) == execution_id
    archive_harvest(
        body="second body",
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(archive),
        execution_id=execution_id,
    )
    assert "second body" in archive.read_text(encoding="utf-8")


def test_archive_harvest_foreign_execution_refused(tmp_path: Path) -> None:
    archive = tmp_path / "harvest.md"
    archive_harvest(
        body="first",
        url="https://claude.ai/new",
        project_uuid="",
        model={"ok": True},
        attested_model="opus-4.8",
        archive_path=str(archive),
        execution_id="exec" + "a" * 28,
    )
    with pytest.raises(RuntimeError, match="foreign execution"):
        archive_harvest(
            body="clobber",
            url="https://claude.ai/new",
            project_uuid="",
            model={"ok": True},
            attested_model="opus-4.8",
            archive_path=str(archive),
            execution_id="exec" + "b" * 28,
        )


def test_emit_detached_status(capsys: pytest.CaptureFixture[str]) -> None:
    abort.emit_detached_status("abc123")
    out = capsys.readouterr().out
    assert "status=detached_remote_running registration_id=abc123" in out


def test_abort_cleanup_non_owner_emits_detached_not_kill(
    isolated_registry,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    r = reg.register_lane(
        holder="remote-driver",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg._release_driver_lock(r.registration_id)
    active = reg._load_active()
    active[r.registration_id] = dict(active[r.registration_id])
    active[r.registration_id]["holder_pid"] = os.getpid() + 99999
    monkeypatch.setattr(reg._store, "load_active", lambda: active)
    monkeypatch.setattr(reg, "is_driver_lock_held", lambda _rid: True)
    killed: list[str] = []
    monkeypatch.setattr(
        abort, "bounded_stop_via_cdp", lambda _url: killed.append("stop")
    )
    monkeypatch.setattr(
        abort, "deregister_on_exit", lambda *_a, **_k: killed.append("kill")
    )
    abort._ABORT_DONE = False
    abort.abort_cleanup(r, purpose="ask")
    assert killed == []
    assert "status=detached_remote_running" in capsys.readouterr().out


def test_abort_cleanup_orphan_reap_noop_on_port_reassign(
    isolated_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="remote-driver",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg._release_driver_lock(r.registration_id)
    killed: list[str] = []
    monkeypatch.setattr(
        abort, "bounded_stop_via_cdp", lambda _url: killed.append("stop")
    )
    monkeypatch.setattr(
        abort, "deregister_on_exit", lambda *_a, **_k: killed.append("kill")
    )
    monkeypatch.setattr(abort, "registration_owns_port", lambda *_a, **_k: False)
    abort._ABORT_DONE = False
    abort.abort_cleanup(r, purpose="ask")
    assert killed == []


def test_abort_cleanup_owner_still_kills(
    isolated_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="owner",
        purpose="ask",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    killed: list[str] = []
    monkeypatch.setattr(
        abort,
        "bounded_stop_via_cdp",
        lambda _url: (
            killed.append("stop") or abort.AttestResult(has_stop=False, probe_ok=True)
        ),
    )
    monkeypatch.setattr(
        abort, "deregister_on_exit", lambda *_a, **_k: killed.append("kill")
    )
    abort._ABORT_DONE = False
    abort.abort_cleanup(r, purpose="ask")
    assert killed == ["stop", "kill"]
    reg._release_driver_lock(r.registration_id)


def test_registration_owns_port_rejects_reassigned(
    isolated_registry, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = reg.register_lane(
        holder="a",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    assert abort.registration_owns_port(r.registration_id, r.port)
    active = reg._load_active()
    active[r.registration_id] = dict(active[r.registration_id])
    active[r.registration_id]["port"] = r.port + 1
    monkeypatch.setattr(reg._store, "load_active", lambda: active)
    assert not abort.registration_owns_port(r.registration_id, r.port)
    reg._release_driver_lock(r.registration_id)


@pytest.mark.asyncio
async def test_submit_composer_draft_auto_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cowork+Auto at re-attest raises; Start task is not clicked."""
    fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "", "text": "Auto", "via": "text"},
        "url": "https://claude.ai/new",
    }

    async def fake_ensure(_page) -> dict:
        return {"ok": False, "after": fp}

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.ensure_approval_auto",
        fake_ensure,
    )
    click = AsyncMock()
    monkeypatch.setattr(
        "claude_bundles.composer_submit.click_submit_button",
        click,
    )
    from claude_bundles.project_ask import _submit_composer_draft

    with pytest.raises(RuntimeError, match="cowork dispatch refused"):
        await _submit_composer_draft(AsyncMock(), composer=AsyncMock(), draft_text="hi")
    click.assert_not_awaited()


@pytest.mark.asyncio
async def test_project_ask_on_page_harvest_incomplete_preserves_body() -> None:
    """a:37226 review A2 — project_ask_on_page must keep last scrape."""
    page = AsyncMock()
    page.url = "https://claude.ai/cowork/cse_01DySavjf3QUK1oDU9NC9McG"
    partial = "Project ask review mid-body. " + ("z" * 150)

    with (
        patch(
            "claude_bundles.project_ask._compose_model_selected",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "claude_bundles.project_ask.harvest_assistant",
            new=AsyncMock(return_value={"count": 1}),
        ),
        patch(
            "claude_bundles.project_ask.send_prompt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask.wait_assistant_reply",
            new=AsyncMock(
                side_effect=HarvestIncompleteError(
                    "timed out incomplete (base_len=0, last=6969, n=6) — ¬delete",
                    body=partial,
                )
            ),
        ),
    ):
        result = await project_ask_on_page(
            page,
            "review prompt",
            project_uuid="019f6917-2ab2-772c-a1ec-f88434b08e32",
            model="opus-5",
            delete_after=False,
            purpose="review",
        )

    assert result.ok is False
    assert result.body == partial
    assert result.body_len == len(partial)
    assert result.body_len > 0
    assert "timed out incomplete" in (result.error or "")


@pytest.mark.asyncio
async def test_send_prompt_induction_fires_for_review_floor() -> None:
    """Marked review prompt submits Use-lines and the packet in one message."""
    from claude_bundles.chat_context_skills import LoadedSkillsReport
    from claude_bundles.project_ask import send_prompt

    page = AsyncMock()
    composer = AsyncMock()
    submit = AsyncMock()
    report = LoadedSkillsReport(
        url="https://claude.ai/chat/x",
        skills=(
            "reasoning-posture",
            "consult-posture",
            "hypothesize-simulate",
        ),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    text = (
        "<!--cdp-required-skills:"
        "reasoning-posture,consult-posture,hypothesize-simulate-->\n"
        "## Review packet\n"
    )
    with (
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
            "claude_bundles.project_ask._submit_composer_draft",
            new=submit,
        ),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=AsyncMock(return_value=report),
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.attest_delivery_channels",
            return_value=[
                "reasoning-posture",
                "consult-posture",
                "hypothesize-simulate",
            ],
        ),
    ):
        await send_prompt(page, text)
    assert submit.await_count == 1
    draft = submit.await_args.kwargs["draft_text"]
    assert "Use the reasoning-posture skill" in draft
    assert "Use the hypothesize-simulate skill" in draft
    assert "## Review packet" in draft


_SEALED = (
    "<!--cdp-required-skills:reasoning-posture,hypothesize-simulate-->\n"
    "Question. Is the desk memo in this turn?\n"
)


def _induction_page() -> tuple[AsyncMock, AsyncMock]:
    page = AsyncMock()
    composer = AsyncMock()
    page.keyboard.insert_text = AsyncMock()
    return page, composer


def _induction_patches(page_composer, report, *, extra=()):
    composer = page_composer
    return (
        patch(
            "claude_bundles.cowork_skill_delivery.partition_cdp_skills",
            return_value=(["reasoning-posture", "hypothesize-simulate"], []),
        ),
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
            "claude_bundles.project_ask._submit_composer_draft",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=AsyncMock(return_value=report),
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.attest_delivery_channels",
            return_value=["reasoning-posture", "hypothesize-simulate"],
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.check_delivery_channels_before_submit",
            return_value=[],
        ),
        *extra,
    )


@pytest.mark.asyncio
async def test_marked_hop_prompt_submits_use_lines_and_body_once() -> None:
    """a:37716 AC1 — one submit carries Use-lines, hop header, and birth id."""
    from claude_bundles.chat_context_skills import LoadedSkillsReport
    from claude_bundles.project_ask import send_prompt

    page, composer = _induction_page()
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture", "hypothesize-simulate"),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    submit = AsyncMock()
    prompt = (
        "<!--cdp-required-skills:reasoning-posture,hypothesize-simulate-->\n"
        "# Hop on agent-bus:14863\n"
        "successor_birth_id: 98d356f4\n"
    )
    with contextlib.ExitStack() as stack:
        for ctx in _induction_patches(
            composer,
            report,
            extra=(
                patch("claude_bundles.project_ask._submit_composer_draft", new=submit),
            ),
        ):
            stack.enter_context(ctx)
        baseline = await send_prompt(page, prompt, await_induction_reply=True)
    assert baseline is None
    assert submit.await_count == 1
    draft = submit.await_args.kwargs["draft_text"]
    assert "Use the reasoning-posture skill" in draft
    assert "Use the hypothesize-simulate skill" in draft
    assert "# Hop on agent-bus:" in draft
    assert "successor_birth_id: 98d356f4" in draft


@pytest.mark.asyncio
async def test_combined_message_does_not_wait_for_induction_reply() -> None:
    """a:37716 AC2 — await_induction_reply does not harvest a second turn."""
    from claude_bundles.chat_context_skills import LoadedSkillsReport
    from claude_bundles.project_ask import project_ask_on_page, send_prompt

    page, composer = _induction_page()
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture", "hypothesize-simulate"),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    capture = AsyncMock()
    with contextlib.ExitStack() as stack:
        for ctx in _induction_patches(
            composer,
            report,
            extra=(
                patch(
                    "claude_bundles.induction_reply_baseline.capture_induction_reply_baseline",
                    new=capture,
                ),
            ),
        ):
            stack.enter_context(ctx)
        baseline = await send_prompt(page, _SEALED, await_induction_reply=True)
    assert baseline is None
    capture.assert_not_awaited()

    caller = {"n": 0, "body_len": 0, "body": ""}
    wait = AsyncMock(
        return_value={"body": "desk answer", "body_len": 40, "n": 1, "url": page.url}
    )
    page.url = "https://claude.ai/cowork/cse_x"
    with (
        patch(
            "claude_bundles.project_ask._compose_model_selected",
            new=AsyncMock(return_value={"ok": True}),
        ),
        patch(
            "claude_bundles.project_ask.harvest_assistant",
            new=AsyncMock(return_value=caller),
        ),
        patch(
            "claude_bundles.project_ask.send_prompt",
            new=AsyncMock(return_value=None),
        ),
        patch("claude_bundles.project_ask.wait_assistant_reply", new=wait),
        patch(
            "claude_bundles.project_ask.resolve_harvest_body",
            new=AsyncMock(
                return_value=type(
                    "H",
                    (),
                    {"content": "desk answer", "provenance": None},
                )()
            ),
        ),
        patch("claude_bundles.project_ask._attest_model", return_value=None),
    ):
        result = await project_ask_on_page(
            page,
            _SEALED,
            project_uuid="019f6917-2ab2-772c-a1ec-f88434b08e32",
            delete_after=False,
            archive_path=None,
        )
    assert result.body == "desk answer"
    assert wait.await_count == 1
    assert wait.await_args.kwargs["before"] == caller


@pytest.mark.asyncio
async def test_unmarked_prompt_keeps_single_body_submit() -> None:
    """a:37716 AC3 — no marker: one submit, no induction panel."""
    from claude_bundles.project_ask import send_prompt

    page, composer = _induction_page()
    submit = AsyncMock()
    panel = AsyncMock()
    with (
        patch("claude_bundles.composer_session_skills.require_compose_surface"),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=composer),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch("claude_bundles.project_ask._submit_composer_draft", new=submit),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=panel,
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
    ):
        await send_prompt(page, "plain body, no marker\n")
    assert submit.await_count == 1
    assert submit.await_args.kwargs["draft_text"] == "plain body, no marker\n"
    panel.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmarked_induction_slugs_still_split_submit() -> None:
    """a:37716 AC3 — unmarked channel slugs keep the induction-then-body submits."""
    from claude_bundles.chat_context_skills import LoadedSkillsReport
    from claude_bundles.project_ask import send_prompt

    page, composer = _induction_page()
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture",),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    submit = AsyncMock()
    with (
        patch(
            "claude_bundles.cowork_skill_delivery.partition_cdp_skills",
            return_value=(["reasoning-posture"], []),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.parse_cdp_sealed_skill_channels",
            return_value=(["reasoning-posture"], [], "body"),
        ),
        patch("claude_bundles.composer_session_skills.require_compose_surface"),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=composer),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch("claude_bundles.project_ask._submit_composer_draft", new=submit),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=AsyncMock(return_value=report),
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
        patch(
            "claude_bundles.cowork_skill_delivery.attest_delivery_channels",
            return_value=["reasoning-posture"],
        ),
    ):
        await send_prompt(page, "/reasoning-posture\nbody")
    assert submit.await_count == 2
    assert (
        "Use the reasoning-posture skill"
        in submit.await_args_list[0].kwargs["draft_text"]
    )


@pytest.mark.asyncio
async def test_marked_empty_induction_list_is_one_body_submit() -> None:
    """a:37716 AC4 — marker with no induction slugs does not open a Use-line turn."""
    from claude_bundles.project_ask import send_prompt

    page, composer = _induction_page()
    submit = AsyncMock()
    panel = AsyncMock()
    text = "<!--cdp-required-skills:path-sim-->\ninline work\n"
    with (
        patch(
            "claude_bundles.cowork_skill_delivery.partition_cdp_skills",
            return_value=([], ["path-sim"]),
        ),
        patch("claude_bundles.composer_session_skills.require_compose_surface"),
        patch(
            "claude_bundles.project_ask.find_composer",
            new=AsyncMock(return_value=composer),
        ),
        patch(
            "claude_bundles.composer_submit.clear_composer_verified",
            new=AsyncMock(),
        ),
        patch("claude_bundles.project_ask._submit_composer_draft", new=submit),
        patch(
            "claude_bundles.project_ask._insert_prompt_text",
            new=AsyncMock(return_value=([], [])),
        ),
        patch(
            "claude_bundles.skill_induction_panel.wait_for_induction_panel",
            new=panel,
        ),
        patch(
            "claude_bundles.skill_context_receipt.record_post_submit_skills_receipt",
            new=AsyncMock(),
        ),
    ):
        await send_prompt(page, text)
    assert submit.await_count == 1
    assert submit.await_args.kwargs["draft_text"] == text
    panel.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_prompt_returns_idle_induction_before_work_paste() -> None:
    """Marked prompts no longer idle-wait before the body (a:37267 split removed by a:37716).

    The split-turn baseline still applies to unmarked induction submits; a marked
    prompt returns None and does not capture an induction ack.
    """
    from claude_bundles.chat_context_skills import LoadedSkillsReport
    from claude_bundles.project_ask import send_prompt

    page, composer = _induction_page()
    capture = AsyncMock(
        return_value={"n": 1, "body": "There's no substantive question", "body_len": 34}
    )
    report = LoadedSkillsReport(
        url="https://claude.ai/cowork/cse_x",
        skills=("reasoning-posture", "hypothesize-simulate"),
        context_found=True,
        skills_heading_found=True,
        model_label=None,
        selectors=(),
        raw_section_text="",
    )
    with contextlib.ExitStack() as stack:
        for ctx in _induction_patches(
            composer,
            report,
            extra=(
                patch(
                    "claude_bundles.induction_reply_baseline.capture_induction_reply_baseline",
                    new=capture,
                ),
            ),
        ):
            stack.enter_context(ctx)
        baseline = await send_prompt(page, _SEALED, await_induction_reply=True)
    assert baseline is None
    capture.assert_not_awaited()


@pytest.mark.asyncio
async def test_marked_submit_raises_unverified_when_panel_fails_after_send() -> None:
    """Panel failure after combined submit → SkillReceiptUnverifiedError (a:37716).

    The message is already submitted; channel attest does not run.
    """
    from claude_bundles.cowork_skill_delivery import SkillReceiptUnverifiedError
    from claude_bundles.project_ask import send_prompt

    page, composer = _induction_page()
    submit = AsyncMock()
    attest = MagicMock()
    with contextlib.ExitStack() as stack:
        for ctx in _induction_patches(
            composer,
            report=AsyncMock(),
            extra=(
                patch("claude_bundles.project_ask._submit_composer_draft", new=submit),
                patch(
                    "claude_bundles.skill_induction_panel.wait_for_induction_panel",
                    new=AsyncMock(side_effect=RuntimeError("panel closed")),
                ),
                patch(
                    "claude_bundles.cowork_skill_delivery.attest_delivery_channels",
                    new=attest,
                ),
                patch(
                    "claude_bundles.cowork_skill_delivery.check_delivery_channels_before_submit",
                    return_value=[],
                ),
            ),
        ):
            stack.enter_context(ctx)
        with pytest.raises(SkillReceiptUnverifiedError, match="panel closed"):
            await send_prompt(page, _SEALED, await_induction_reply=True)
    assert submit.await_count == 1
    draft = submit.await_args.kwargs["draft_text"]
    assert "Question. Is the desk memo" in draft
    attest.assert_not_called()


def test_induction_ack_is_not_complete_against_its_own_baseline() -> None:
    """purpose=ask seals the skill ack when base_n is the pre-send snapshot."""
    from claude_bundles.chat_reply_wait import _complete_enough
    from claude_bundles.induction_reply_baseline import work_reply_before

    ack = {
        "n": 1,
        "body_len": 80,
        "body": "There's no substantive question in your message yet",
        "streaming": False,
        "stop": False,
    }
    caller_before = {"n": 0, "body_len": 0}
    assert (
        _complete_enough(
            ack,
            base_len=0,
            base_n=caller_before["n"],
            min_growth=1,
            min_body=1,
        )
        is True
    )
    baseline = work_reply_before(caller_before, ack)
    assert (
        _complete_enough(
            ack,
            base_len=0,
            base_n=baseline["n"],
            min_growth=1,
            min_body=1,
        )
        is False
    )
    work = {**ack, "n": 2, "body": "Desk memo answer", "body_len": 17}
    assert (
        _complete_enough(
            work,
            base_len=ack["body_len"],
            base_n=baseline["n"],
            min_growth=1,
            min_body=1,
        )
        is True
    )
