"""Offline tests for compose attestation + warm live submit discovery (25291)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright")
from playwright.async_api import async_playwright  # noqa: E402

from claude_bundles.chat_cowork_mode import select_compose_mode
from claude_bundles.compose_attest import (
    _AUTO_ARIA_RE,
    _compose_attested,
    approval_label,
    approval_mode_chip,
    compose_mode_fingerprint,
    cowork_auto_refuse_reason,
    discover_live_submit,
    dispatch_approval_refuse_reason,
    is_excluded_submit_control,
    is_positive_submit_match,
    pick_submit_candidate,
    resolve_submit_strategy,
    warm_submit_settle_ms,
)

pytestmark = pytest.mark.offline


_WARM_CSE_HTML = """
<!doctype html><html><head><title>Mid-session task - Claude</title></head><body>
<main>
  <div class="compose-shell">
    <button aria-label="Model: Sonnet 5 High">Sonnet 5</button>
    <button aria-label="Automatically approve">Auto</button>
    <button aria-label="Attach file">Attach</button>
    <div data-testid="chat-input" contenteditable="true" style="min-height:80px;width:400px"></div>
    <button aria-label="Send"></button>
  </div>
</main>
</body></html>
"""

_BARE_NEW_COWORK_HTML = """
<!doctype html><html><head><title>New task - Claude</title></head><body>
<main>
  <button aria-label="Model: Opus 4.8 High">Opus</button>
  <button aria-label="Automatically approve">Auto</button>
  <div data-testid="chat-input" contenteditable="true" style="min-height:80px;width:400px"></div>
  <button aria-label="Start task">Start task</button>
</main>
</body></html>
"""

# a:31319 — live census against a fresh /new tab (2026-08-30) found the
# approval chip with NO aria-label at all, just plain text "Auto". This
# fixture reproduces that exact shape (falsifies the private-use-area-prefix
# theory floated in the friction — text is plain ASCII).
_BARE_NEW_COWORK_ARIA_LESS_HTML = """
<!doctype html><html><head><title>New task - Claude</title></head><body>
<main>
  <button aria-label="Model: Opus 4.8 High">Opus</button>
  <button>Auto</button>
  <div data-testid="chat-input" contenteditable="true" style="min-height:80px;width:400px"></div>
  <button aria-label="Start task">Start task</button>
</main>
</body></html>
"""


def test_resolve_submit_strategy_warm_cse() -> None:
    url = "https://claude.ai/cowork/cse_01MB8QV81GvCcRLZkX1F5MJY"
    assert resolve_submit_strategy(url, {"mode": None}) == "live_discover"


def test_resolve_submit_strategy_warm_chat() -> None:
    assert resolve_submit_strategy("https://claude.ai/chat/abc", {}) == "live_discover"


def test_resolve_submit_strategy_bare_new() -> None:
    assert (
        resolve_submit_strategy("https://claude.ai/new", {"mode": "cowork"})
        == "live_discover"
    )


def test_resolve_submit_strategy_v2_new_session_uses_live_discover() -> None:
    assert (
        resolve_submit_strategy(
            "https://claude.ai/new",
            {
                "title_fingerprint": "new_session",
                "mode": "chat",
                "compose_readiness": {"ready": True},
            },
        )
        == "live_discover"
    )
    assert (
        resolve_submit_strategy("https://claude.ai/new", {"mode": "cowork"})
        == "live_discover"
    )


def test_resolve_submit_strategy_code_surface() -> None:
    assert (
        resolve_submit_strategy(
            "https://claude.ai/code",
            {"mode": None, "approval": {"text": "Auto"}},
            surface="code",
        )
        == "live_discover"
    )


def test_is_excluded_submit_control_model_and_approval() -> None:
    assert is_excluded_submit_control(aria="Model: Sonnet 5 High")
    assert is_excluded_submit_control(aria="Automatically approve")
    assert is_excluded_submit_control(aria="Attach file")
    assert is_excluded_submit_control(aria="Stop generating")


def test_is_positive_submit_match_warm_send() -> None:
    assert is_positive_submit_match(aria="Send")
    assert is_positive_submit_match(aria="Send message")
    assert is_positive_submit_match(aria="Start task")
    assert not is_positive_submit_match(aria="Attach file")


def test_pick_submit_candidate_prefers_send_over_attach() -> None:
    candidates = [
        {"aria": "Attach file", "text": "", "name": "Attach file"},
        {"aria": "Send", "text": "", "name": "Send"},
    ]
    hit = pick_submit_candidate(candidates)
    assert hit is not None
    assert hit["name"] == "Send"
    assert hit["pick"] == "positive_match"


def test_pick_submit_candidate_unique_non_excluded() -> None:
    candidates = [
        {"aria": "Attach file", "text": "", "name": "Attach file"},
        {"aria": "", "text": "Go", "name": "Go"},
    ]
    hit = pick_submit_candidate(candidates)
    assert hit is not None
    assert hit["name"] == "Go"
    assert hit["pick"] == "unique_candidate"


def test_pick_submit_candidate_ambiguous_returns_none() -> None:
    candidates = [
        {"aria": "", "text": "Go", "name": "Go"},
        {"aria": "", "text": "Next", "name": "Next"},
    ]
    assert pick_submit_candidate(candidates) is None


def test_warm_submit_settle_ms_documented() -> None:
    assert warm_submit_settle_ms() == 300


@pytest.mark.asyncio
async def test_discover_live_submit_warm_cse_finds_send() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_WARM_CSE_HTML)
            hit = await discover_live_submit(page)
            assert hit["ok"] is True
            assert hit["via"] == "composer_local"
            assert hit["name"] == "Send"
            assert hit["pick"] == "positive_match"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_discover_live_submit_bare_new_finds_start_task() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_BARE_NEW_COWORK_HTML)
            hit = await discover_live_submit(page)
            assert hit["ok"] is True
            assert hit["name"] == "Start task"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_select_compose_mode_polls_until_fingerprint_attests_cowork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chip absent at t=0; fingerprint attests cowork during poll — not chip_missing."""
    fp_calls = {"n": 0}
    chat_fp = {
        "title": "New chat - Claude",
        "mode": "chat",
        "approval": None,
        "url": "https://claude.ai/new",
    }
    cowork_fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "Automatically approve", "text": "Auto"},
        "url": "https://claude.ai/new",
    }

    async def mock_fingerprint(_page) -> dict:
        fp_calls["n"] += 1
        return chat_fp if fp_calls["n"] <= 2 else cowork_fp

    async def mock_try_click(_page, _label):
        return None, {
            "gate_rejects": [],
            "surface_radiogroup_count": 0,
            "radiogroup_names": [],
        }

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.compose_mode_fingerprint",
        mock_fingerprint,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.try_click_compose_chip",
        mock_try_click,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()

    result = await select_compose_mode(page, "cowork")
    assert result["ok"] is True
    assert result["step"] == "already_cowork"
    assert result["after"]["mode"] == "cowork"


@pytest.mark.asyncio
async def test_select_compose_mode_polls_until_chip_click_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chip absent at t=0, present after poll — click then attest, not chip_missing."""
    click_calls = {"n": 0}
    chat_fp = {
        "title": "New chat - Claude",
        "mode": "chat",
        "approval": None,
        "url": "https://claude.ai/new",
    }
    cowork_fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "Automatically approve", "text": "Auto"},
        "url": "https://claude.ai/new",
    }

    async def mock_fingerprint(_page) -> dict:
        return chat_fp

    async def mock_try_click(_page, _label):
        click_calls["n"] += 1
        probe = {
            "gate_rejects": [],
            "surface_radiogroup_count": 1,
            "radiogroup_names": ["Surface"],
        }
        if click_calls["n"] < 3:
            return None, probe
        return "playwright_surface", {**probe, "via": "playwright_surface"}

    async def mock_attest(
        _page, mode, *, timeout_s=8.0, poll_ms=400, **_kwargs
    ) -> dict:
        return {"ok": True, "step": f"attested_{mode}", "fingerprint": cowork_fp}

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.compose_mode_fingerprint",
        mock_fingerprint,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.try_click_compose_chip",
        mock_try_click,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.await_compose_attest",
        mock_attest,
    )
    page = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.evaluate = AsyncMock(return_value=[])

    result = await select_compose_mode(page, "cowork")
    assert result["ok"] is True
    assert result["step"] == "selected_cowork"
    assert result["step"] != "chip_missing"


_NEW_SESSION_TITLE_HTML = """
<!doctype html><html><head><title>New session - Claude</title></head><body>
<button>Skip</button>
</body></html>
"""


@pytest.mark.asyncio
async def test_compose_mode_fingerprint_accepts_new_session_title() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_NEW_SESSION_TITLE_HTML)
            fp = await compose_mode_fingerprint(page)
            assert fp["title"] == "New session - Claude"
            assert fp.get("title_fingerprint") == "new_session"
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_compose_mode_fingerprint_falls_back_to_text_when_aria_absent() -> None:
    """a:31319 — aria-less "Auto" button must still fingerprint as approval."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_BARE_NEW_COWORK_ARIA_LESS_HTML)
            fp = await compose_mode_fingerprint(page)
            assert fp["mode"] == "cowork"
            assert fp["approval"] is not None
            assert fp["approval"]["aria"] == ""
            assert fp["approval"]["text"] == "Auto"
            assert fp["approval"]["via"] == "text"
        finally:
            await browser.close()


def test_approval_label_prefers_aria_falls_back_to_text() -> None:
    aria_fp = {"approval": {"aria": "Automatically approve", "text": "Auto"}}
    text_fp = {"approval": {"aria": "", "text": "Auto"}}
    skip_lb_fp = {
        "approval": {
            "aria": "",
            "text": "Skip",
            "labelledby": "Permission mode: Skip all approvals",
            "via": "text",
        }
    }
    assert approval_label(aria_fp) == "Automatically approve"
    assert approval_label(text_fp) == "Auto"
    assert approval_label(skip_lb_fp) == "Permission mode: Skip all approvals"
    assert approval_label({"approval": None}) == ""


@pytest.mark.parametrize("mode", ["chat", "cowork"])
def test_compose_attested_no_chip_refuses_require_auto(mode: str) -> None:
    fp = {
        "title": f"New {'chat' if mode == 'chat' else 'task'} - Claude",
        "mode": mode,
        "approval": None,
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, mode, require_auto=True) is False
    assert _compose_attested(fp, mode, require_auto=False) is True
    reason = cowork_auto_refuse_reason(fp)
    assert reason is not None
    assert "Permission mode Skip or Auto not attested" in reason
    assert dispatch_approval_refuse_reason(fp) == reason


def test_compose_attested_cowork_skip_text_only_legacy_still_passes() -> None:
    """Legacy Skip-all still satisfies the ship gate."""
    fp = {
        "title": "New task - Claude",
        "mode": "cowork",
        "approval": {"aria": "", "text": "Skip", "via": "text"},
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, "cowork") is True
    assert cowork_auto_refuse_reason(fp) is None


def test_compose_attested_cowork_auto_text_only_passes_ship_gate() -> None:
    """v2 Permission mode Auto is the ship gate (a:43854-d)."""
    fp = {
        "title": "New session - Claude",
        "mode": "chat",
        "approval": {"aria": "", "text": "Auto", "via": "text"},
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, "cowork") is True
    assert cowork_auto_refuse_reason(fp) is None


def test_cowork_auto_refuse_reason_text_only_manual_still_refuses() -> None:
    """Text-only fallback must not silently pass a stuck-Manual chip either."""
    fp = {
        "title": "New session - Claude",
        "mode": "chat",
        "approval": {"aria": "", "text": "Manual", "via": "text"},
        "url": "https://claude.ai/new",
    }
    assert _compose_attested(fp, "cowork") is False
    reason = cowork_auto_refuse_reason(fp)
    assert reason is not None
    assert "Permission mode Skip or Auto" in reason


def _approval_fp(
    *,
    text: str | None,
    mode: str | None = "cowork",
    url: str = "https://claude.ai/new",
) -> dict:
    approval = None
    if text is not None:
        approval = {"aria": "", "text": text, "via": "text"}
    title = "Code - Claude" if "/code" in url else f"New {mode or 'task'} - Claude"
    return {
        "title": title,
        "mode": mode,
        "approval": approval,
        "url": url,
    }


def _code_fp(*, text: str | None) -> dict:
    return _approval_fp(text=text, mode=None, url="https://claude.ai/code")


@pytest.mark.parametrize(
    ("surface", "chip_text", "mode", "expect_refuse"),
    [
        ("cowork", "Skip", "cowork", False),
        ("cowork", "Auto", "cowork", False),
        ("cowork", "Manual", "cowork", True),
        ("code", "Skip", None, False),
        ("code", "Auto", None, False),
        ("code", "Manual", None, True),
    ],
)
def test_dispatch_approval_refuse_table_cowork_and_code(
    surface: str, chip_text: str, mode: str | None, expect_refuse: bool
) -> None:
    if surface == "code":
        fp = _code_fp(text=chip_text)
    else:
        fp = _approval_fp(text=chip_text, mode=mode or "cowork")
    reason = dispatch_approval_refuse_reason(fp, surface=surface)
    if expect_refuse:
        assert reason is not None
        if surface == "code":
            assert reason.startswith("code dispatch refused:")
            assert "code accepts skip, auto" in reason or "not attested" in reason
        else:
            assert "cowork dispatch refused" in reason
    else:
        assert reason is None


def test_dispatch_approval_refuse_code_no_chip_passes() -> None:
    fp = _code_fp(text=None)
    assert dispatch_approval_refuse_reason(fp, surface="code") is None
    assert approval_mode_chip(fp) == "absent"


def test_approval_mode_chip_unknown_vs_absent() -> None:
    assert approval_mode_chip(_code_fp(text=None)) == "absent"
    fp = _code_fp(text="Prompt me")
    assert approval_mode_chip(fp) == "unknown"
    reason = dispatch_approval_refuse_reason(fp, surface="code")
    assert reason is not None
    assert "Permission mode Skip or Auto not attested" in reason


def test_code_manual_refusal_message_names_surface_and_chip() -> None:
    reason = dispatch_approval_refuse_reason(_code_fp(text="Manual"), surface="code")
    assert reason is not None
    assert "surface=code" in reason
    assert "'Manual'" in reason
    assert "code accepts skip, auto" in reason


# Census cortex://notes/tmp/dom-samples/.../permission-menu-closed-and-open.md b25894d3
_PERM_V2_MANUAL_HTML = """
<!doctype html><html><head><title>New session - Claude</title></head><body>
<div data-testid="chat-input" contenteditable="true"></div>
<button id="perm" type="button" data-cds="Button" aria-haspopup="menu" aria-expanded="false"
  tabindex="0" style="width:68.125px;height:28px">Manual</button>
<div id="menu" role="menu" style="display:none">
  <div role="group" aria-labelledby="perm-group-label">
    <div id="perm-group-label" aria-hidden="true">Permission mode</div>
    <div role="menuitemradio" id="row-manual" aria-checked="true" tabindex="-1">
      <span class="flex min-w-0 flex-1 flex-col">
        <span class="truncate"><span>Manual</span></span>
        <span class="text-footnote text-muted">Claude asks before using new tools or opening new sites.</span>
      </span>
    </div>
    <div role="menuitemradio" id="row-auto" aria-checked="false" tabindex="-1">
      <span class="flex min-w-0 flex-1 flex-col">
        <span class="truncate"><span>Auto</span></span>
        <span class="text-footnote text-muted">Claude runs on its own and pauses to ask if anything looks unsafe.</span>
      </span>
    </div>
  </div>
</div>
<script>
document.getElementById('perm').addEventListener('click', () => {
  document.getElementById('menu').style.display = 'block';
  document.getElementById('perm').setAttribute('aria-expanded', 'true');
});
function setAuto() {
  document.getElementById('perm').innerText = 'Auto';
  document.getElementById('row-manual').setAttribute('aria-checked', 'false');
  document.getElementById('row-auto').setAttribute('aria-checked', 'true');
  document.getElementById('menu').style.display = 'none';
  document.getElementById('perm').setAttribute('aria-expanded', 'false');
}
document.getElementById('row-auto').addEventListener('click', setAuto);
</script>
</body></html>
"""

_PERM_V2_AUTO_HTML = (
    _PERM_V2_MANUAL_HTML.replace(
        'style="width:68.125px;height:28px">Manual</button>',
        'style="width:68.125px;height:28px">Auto</button>',
    )
    .replace('aria-checked="true">Manual', 'aria-checked="false">Manual', 1)
    .replace('aria-checked="false">Auto', 'aria-checked="true">Auto', 1)
)

_PERM_V2_SET_FAIL_HTML = _PERM_V2_MANUAL_HTML.replace(
    "document.getElementById('row-auto').addEventListener('click', setAuto);",
    "document.getElementById('row-auto').addEventListener('click', () => {});",
)

# cortex://notes/tmp/dom-samples/claude.ai/chat/permission-chip/2026-11-13/existing-chat-skip-probe (5e26ea76)
_PERM_V2_SKIP_HTML = """
<!doctype html><html><head><title>Chat - Claude</title></head><body>
<div data-testid="chat-input" contenteditable="true"></div>
<span id="perm-chip-label" class="sr-only">Permission mode: Skip all approvals</span>
<button id="perm" type="button" data-cds="Button" aria-haspopup="menu" aria-expanded="false"
  aria-labelledby="perm-chip-label" tabindex="0" style="width:52px;height:28px">Skip</button>
</body></html>
"""


_A84028F3_AUTO_LABELLEDBY_FP = {
    "title": "New session - Claude",
    "mode": "chat",
    "approval": {
        "aria": "",
        "text": "Auto",
        "via": "text",
        "labelledby": "Permission mode: Auto",
    },
    "url": "https://claude.ai/new",
}

_V2_MANUAL_LABELLEDBY_FP = {
    "title": "New session - Claude",
    "mode": "chat",
    "approval": {
        "aria": "",
        "text": "Manual",
        "via": "text",
        "labelledby": "Permission mode: Manual",
    },
    "url": "https://claude.ai/new",
}


def test_v2_auto_labelledby_passes_ship_gate_a84028f3() -> None:
    assert approval_label(_A84028F3_AUTO_LABELLEDBY_FP) == "Permission mode: Auto"
    assert approval_mode_chip(_A84028F3_AUTO_LABELLEDBY_FP) == "auto"
    assert cowork_auto_refuse_reason(_A84028F3_AUTO_LABELLEDBY_FP) is None
    assert (
        dispatch_approval_refuse_reason(_A84028F3_AUTO_LABELLEDBY_FP, surface="cowork")
        is None
    )
    assert (
        dispatch_approval_refuse_reason(_A84028F3_AUTO_LABELLEDBY_FP, surface="code")
        is None
    )
    assert _compose_attested(_A84028F3_AUTO_LABELLEDBY_FP, "cowork") is True


def test_v2_manual_labelledby_classified_as_manual_not_unknown() -> None:
    assert approval_label(_V2_MANUAL_LABELLEDBY_FP) == "Permission mode: Manual"
    assert approval_mode_chip(_V2_MANUAL_LABELLEDBY_FP) == "manual"
    cowork_reason = dispatch_approval_refuse_reason(
        _V2_MANUAL_LABELLEDBY_FP, surface="cowork"
    )
    assert (
        cowork_reason
        == "cowork dispatch refused: approval 'Manual' (need Permission mode Skip or Auto)"
    )
    code_reason = dispatch_approval_refuse_reason(
        _V2_MANUAL_LABELLEDBY_FP, surface="code"
    )
    assert (
        code_reason
        == "code dispatch refused: surface=code approval 'Manual' (code accepts skip, auto)"
    )
    assert _compose_attested(_V2_MANUAL_LABELLEDBY_FP, "cowork") is False


def test_auto_aria_re_does_not_match_automate_prefix() -> None:
    assert not _AUTO_ARIA_RE.search("Automate")


def test_compose_attested_chat_with_manual_chip_and_readiness_refuses() -> None:
    fp = {
        "mode": "chat",
        "approval": {
            "aria": "",
            "text": "Manual",
            "labelledby": "Permission mode: Manual",
            "via": "text",
        },
        "compose_readiness": {"ready": True},
    }
    assert _compose_attested(fp, "chat", require_auto=True) is False
    assert _compose_attested(fp, "chat", require_auto=False) is True


@pytest.mark.parametrize(
    ("fp", "expect"),
    [
        (
            {
                "mode": "chat",
                "approval": {
                    "aria": "",
                    "text": "Skip",
                    "labelledby": "Permission mode: Skip all approvals",
                    "via": "text",
                },
                "compose_readiness": {"ready": True},
            },
            True,
        ),
        (_A84028F3_AUTO_LABELLEDBY_FP, True),
        (_V2_MANUAL_LABELLEDBY_FP, False),
        (
            {
                "mode": "cowork",
                "approval": {"aria": "", "text": "", "via": "text"},
                "compose_readiness": {"ready": True},
            },
            False,
        ),
    ],
)
def test_compose_attested_ship_gate_with_readiness(fp: dict, expect: bool) -> None:
    assert _compose_attested(fp, "cowork", require_auto=True) is expect


def test_v2_skip_labelledby_passes_ship_gate() -> None:
    fp = {
        "title": "Chat - Claude",
        "mode": "chat",
        "approval": {
            "aria": "",
            "text": "Skip",
            "labelledby": "Permission mode: Skip all approvals",
            "via": "text",
        },
        "url": "https://claude.ai/chat/x",
    }
    assert approval_mode_chip(fp) == "skip"
    assert cowork_auto_refuse_reason(fp) is None
    assert _compose_attested(fp, "cowork") is True


@pytest.mark.asyncio
async def test_compose_mode_fingerprint_v2_existing_chat_skip_probe() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_V2_SKIP_HTML)
            fp = await compose_mode_fingerprint(page)
            assert fp["approval"] is not None
            assert fp["approval"]["text"] == "Skip"
            assert fp["approval"]["labelledby"] == "Permission mode: Skip all approvals"
            assert cowork_auto_refuse_reason(fp) is None
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_approval_auto_v2_already_skip_noop() -> None:
    from claude_bundles.chat_cowork_mode import ensure_approval_auto

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_V2_SKIP_HTML)
            before = (await page.locator("#perm").inner_text()).strip()
            result = await ensure_approval_auto(page)
            after = (await page.locator("#perm").inner_text()).strip()
            assert result["ok"] is True
            assert result["step"] == "permission_skip"
            assert before == after == "Skip"
            assert cowork_auto_refuse_reason(result["after"]) is None
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_approval_auto_v2_manual_flips_to_auto() -> None:
    from claude_bundles.chat_cowork_mode import ensure_approval_auto

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_V2_MANUAL_HTML)
            result = await ensure_approval_auto(page)
            assert result["ok"] is True
            assert result["after"]["approval"]["text"] == "Auto"
            assert cowork_auto_refuse_reason(result["after"]) is None
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_ensure_approval_auto_v2_already_auto_noop() -> None:
    from claude_bundles.chat_cowork_mode import ensure_approval_auto

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_V2_AUTO_HTML)
            result = await ensure_approval_auto(page)
            assert result["ok"] is True
            assert result["step"] == "permission_auto"
            assert cowork_auto_refuse_reason(result["after"]) is None
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_cowork_submit_refuses_when_permission_stays_manual() -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_V2_SET_FAIL_HTML)
            from claude_bundles.project_ask import _submit_composer_draft

            with pytest.raises(RuntimeError, match="Permission mode Skip or Auto"):
                await _submit_composer_draft(
                    page,
                    composer=page.locator("[data-testid=chat-input]"),
                    draft_text="hi",
                    claude_surface="cowork",
                )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_cowork_submit_passes_after_permission_auto_attested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(_PERM_V2_MANUAL_HTML)
            monkeypatch.setattr(
                "claude_bundles.composer_submit.submit_composer_content",
                AsyncMock(),
            )
            monkeypatch.setattr(
                "claude_bundles.project_ask.resolve_submit_strategy",
                lambda *a, **k: "live_discover",
            )
            from claude_bundles.project_ask import _submit_composer_draft

            await _submit_composer_draft(
                page,
                composer=page.locator("[data-testid=chat-input]"),
                draft_text="hi",
                claude_surface="cowork",
            )
        finally:
            await browser.close()


@pytest.mark.asyncio
async def test_code_surface_never_awaits_ensure_approval_auto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fp = _code_fp(text="Auto")

    async def fail_if_called(_page) -> dict:
        raise AssertionError("ensure_approval_auto must not run on code surface")

    async def fake_fingerprint(_page) -> dict:
        return fp

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.ensure_approval_auto",
        fail_if_called,
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask.compose_mode_fingerprint",
        fake_fingerprint,
    )
    monkeypatch.setattr(
        "claude_bundles.composer_submit.submit_composer_content",
        AsyncMock(),
    )
    from claude_bundles.project_ask import _submit_composer_draft

    receipt: dict = {}
    await _submit_composer_draft(
        AsyncMock(),
        composer=AsyncMock(),
        draft_text="hi",
        claude_surface="code",
        compose_receipt=receipt,
    )
    assert receipt["approval_mode"] == "auto"


@pytest.mark.asyncio
async def test_compose_receipt_approval_mode_only_on_pass_not_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fp = _approval_fp(text="Manual", mode="cowork")

    async def fake_ensure(_page) -> dict:
        return {"ok": False, "after": fp}

    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.ensure_approval_auto",
        fake_ensure,
    )
    from claude_bundles.project_ask import _submit_composer_draft

    receipt: dict = {"model_ran": "Sonnet"}
    with pytest.raises(RuntimeError, match="Permission mode Skip or Auto"):
        await _submit_composer_draft(
            AsyncMock(),
            composer=AsyncMock(),
            draft_text="hi",
            claude_surface="cowork",
            compose_receipt=receipt,
        )
    assert receipt == {"model_ran": "Sonnet"}


def _new_session_pre_readiness_fp(
    *,
    chip_text: str | None = None,
    url: str = "https://claude.ai/new",
    include_title_fp: bool = True,
) -> dict:
    approval = (
        None if chip_text is None else {"aria": "", "text": chip_text, "via": "text"}
    )
    fp: dict = {
        "title": "New session - Claude" if include_title_fp else "Claude",
        "mode": None,
        "approval": approval,
        "url": url,
    }
    if include_title_fp:
        fp["title_fingerprint"] = "new_session"
    return fp


@pytest.mark.parametrize("surface", ["cowork", "code"])
@pytest.mark.parametrize(
    ("url", "include_title_fp"),
    [
        ("https://claude.ai/new", True),
        ("about:blank", True),
        ("https://claude.ai/new", False),
    ],
)
def test_new_session_pre_readiness_no_chip_refuses_cowork_and_code(
    surface: str,
    url: str,
    include_title_fp: bool,
) -> None:
    """a:43854-g r3 — pre-readiness /new or new_session with mode=None and no chip refuses."""
    fp = _new_session_pre_readiness_fp(
        chip_text=None, url=url, include_title_fp=include_title_fp
    )
    reason = dispatch_approval_refuse_reason(fp, surface=surface)
    assert reason is not None
    assert "Permission mode Skip or Auto not attested" in reason
    assert cowork_auto_refuse_reason(fp) is not None
    assert _compose_attested(fp, "cowork", require_auto=True) is False
    assert _compose_attested(fp, "chat", require_auto=True) is False


@pytest.mark.parametrize("surface", ["cowork", "code"])
@pytest.mark.asyncio
async def test_submit_composer_draft_new_session_pre_readiness_no_chip_refuses(
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
) -> None:
    """Pre-readiness new_session without chip must not leak through live_discover."""
    fp = _new_session_pre_readiness_fp(chip_text=None)
    submit = AsyncMock()
    monkeypatch.setattr(
        "claude_bundles.composer_submit.submit_composer_content",
        submit,
    )
    monkeypatch.setattr(
        "claude_bundles.chat_cowork_mode.ensure_approval_auto",
        AsyncMock(return_value={"ok": False, "after": fp}),
    )
    monkeypatch.setattr(
        "claude_bundles.project_ask.compose_mode_fingerprint",
        AsyncMock(return_value=fp),
    )
    from claude_bundles.project_ask import _submit_composer_draft

    page = AsyncMock()
    page.url = "https://claude.ai/new"
    with pytest.raises(RuntimeError, match="Permission mode Skip or Auto not attested"):
        await _submit_composer_draft(
            page,
            composer=AsyncMock(),
            draft_text="hi",
            claude_surface=surface,
        )
    submit.assert_not_awaited()


@pytest.mark.parametrize(
    "project_url",
    [
        "https://claude.ai/project/abc",
        "https://claude.ai/cowork/project/019f6917-2ab2-772c-a1ec-f88434b08e32",
    ],
)
def test_project_shell_no_chip_still_returns_none(project_url: str) -> None:
    """True project-shell URLs (mode=None, no new_* fingerprint) keep returning None."""
    fp = {
        "title": "Project - Claude",
        "mode": None,
        "approval": None,
        "url": project_url,
    }
    assert cowork_auto_refuse_reason(fp) is None
    assert dispatch_approval_refuse_reason(fp, surface="cowork") is None
    assert dispatch_approval_refuse_reason(fp, surface="code") is None


@pytest.mark.parametrize(
    ("chip_text", "expect_refuse"),
    [
        ("Skip", False),
        ("Auto", False),
        ("Manual", True),
    ],
)
@pytest.mark.parametrize("surface", ["cowork", "code"])
def test_new_session_pre_readiness_skip_auto_pass_and_manual_refuses(
    chip_text: str,
    expect_refuse: bool,
    surface: str,
) -> None:
    """On pre-readiness new_session (mode=None), Skip/Auto pass and Manual refuses."""
    fp = _new_session_pre_readiness_fp(chip_text=chip_text)
    reason = dispatch_approval_refuse_reason(fp, surface=surface)
    cowork_reason = cowork_auto_refuse_reason(fp)
    if expect_refuse:
        assert reason is not None
        assert cowork_reason is not None
        assert _compose_attested(fp, "cowork", require_auto=True) is False
        assert _compose_attested(fp, "chat", require_auto=True) is False
    else:
        assert reason is None
        assert cowork_reason is None
        assert _compose_attested(fp, "cowork", require_auto=True) is True
        assert _compose_attested(fp, "chat", require_auto=True) is True
