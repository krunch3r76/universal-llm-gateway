"""Submit-without-text must match ourDraftSig in generated JS."""

from cursor_bridge.dom_composer_paste import (
    _click_send_own_draft_body,
    operator_busy_probe_js,
)


def test_operator_busy_probe_uses_ordered_selectors() -> None:
    js = operator_busy_probe_js(
        selector="[role='textbox'], [contenteditable='true']",
    )
    assert "for (const s of sels)" in js
    assert "document.querySelector(sel)" in js
    assert "document.querySelector(" not in js.split("for (const s of sels)")[0]


def test_click_send_own_draft_checks_sig() -> None:
    js = _click_send_own_draft_body("[contenteditable='true']")
    assert "ourDraftSig" in js
    assert "draft_not_ours" in js
