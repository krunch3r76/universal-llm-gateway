"""Submit-without-text must match ourDraftSig in generated JS."""

from cursor_bridge.dom_composer_paste import (
    _click_send_body,
    _click_send_own_draft_body,
    _set_composer_body,
    composer_text_semantic_equals,
    normalize_composer_text,
    operator_busy_probe_js,
)


def test_operator_busy_probe_uses_ordered_selectors() -> None:
    js = operator_busy_probe_js(
        selector="[role='textbox'], [contenteditable='true']",
    )
    assert "for (const s of sels)" in js
    assert "document.querySelector(s)" in js
    assert "document.querySelector(" not in js.split("for (const s of sels)")[0]


def test_draft_sig_writes_clear_our_draft_writer() -> None:
    """Marking or clearing ourDraftSig also sets ourDraftWriter to empty."""
    from cursor_bridge.cdp_composer_input import (
        _mark_our_draft_sig_js,
        clear_our_composer_draft_js,
    )
    from cursor_bridge.dom_composer_paste import clear_composer_draft_dom_no_focus_js

    marked = _mark_our_draft_sig_js("[contenteditable='true']", "hello")
    cleared = clear_our_composer_draft_js(
        selector="[contenteditable='true']", watermark="WAKE"
    )
    no_focus = clear_composer_draft_dom_no_focus_js("[contenteditable='true']")
    for js in (marked, cleared, no_focus):
        assert "ourDraftSig" in js
        assert "g.ourDraftWriter='';" in js


def test_click_send_own_draft_checks_sig() -> None:
    js = _click_send_own_draft_body("[contenteditable='true']")
    assert "ourDraftSig" in js
    assert "draft_not_ours" in js


def test_composer_text_semantic_equals_nfc_nbsp_and_newlines() -> None:
    assert composer_text_semantic_equals("café", "caf\u00e9")
    assert composer_text_semantic_equals("a\u00a0b", "a b")
    assert composer_text_semantic_equals("line1\nline2", "line1\nline2")
    assert composer_text_semantic_equals("  spaced  ", "spaced")
    assert not composer_text_semantic_equals("alpha", "beta")
    assert normalize_composer_text("x\r\ny") == "x\ny"


def test_dom_js_includes_semantic_normalizer() -> None:
    set_js = _set_composer_body("[contenteditable='true']", "hello", draft_writer="w")
    click_js = _click_send_body("[contenteditable='true']", "hello")
    assert "ulgNormalizeComposerText" in set_js
    assert "ulgNormalizeComposerText" in click_js
