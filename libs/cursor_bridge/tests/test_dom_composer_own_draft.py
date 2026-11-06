"""Submit-without-text must match ourDraftSig in generated JS."""

import pytest
from cursor_bridge.dom_composer_paste import (
    _click_send_body,
    _click_send_own_draft_body,
    _set_composer_body,
    composer_text_semantic_equals,
    dom_insert_and_send,
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
    assert composer_text_semantic_equals("  spaced  ", "  spaced  ")
    assert not composer_text_semantic_equals("  spaced  ", "spaced")
    assert not composer_text_semantic_equals("a\nb", "a\n\nb")
    assert not composer_text_semantic_equals("alpha", "beta")
    assert normalize_composer_text("x\r\ny") == "x\ny"


def test_dom_js_includes_semantic_normalizer() -> None:
    set_js = _set_composer_body("[contenteditable='true']", "hello", draft_writer="w")
    click_js = _click_send_body("[contenteditable='true']", "hello")
    assert "ulgNormalizeComposerText" in set_js
    assert "ulgPlainHtml" in set_js
    assert "white-space:pre-wrap" in set_js
    assert "setContent(line)" not in set_js
    assert "el.textContent=line" not in set_js
    assert "ulgNormalizeComposerText" in click_js
    assert "ulgComposerDomEmpty" in set_js


def test_tiptap_empty_placeholder_allows_insert_and_foreign_whitespace_blocks() -> None:
    """Live-observed empty TipTap uses innerText newline; human spaces must stay protected."""
    from playwright.sync_api import sync_playwright

    writer = "interlocutor:agent"
    empty_html = (
        '<p class="is-empty is-editor-empty">'
        '<br class="ProseMirror-trailingBreak">'
        "</p>"
    )
    foreign_html = '<p class="is-editor-empty">  human note  </p>'

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()

        empty_row = page.evaluate(
            """({js, html, writer}) => {
              document.body.innerHTML = '<form><div data-composer="1"></div></form>';
              const el = document.querySelector('[data-composer]');
              el.innerHTML = html;
              let inner = '\\n';
              Object.defineProperty(el, 'innerText', {
                get() { return inner; },
                set(v) { inner = v; },
                configurable: true,
              });
              let calls = 0;
              el.editor = {isEmpty: true, commands: {setContent(h) {
                calls += 1;
                el.innerHTML = h;
                inner = 'probe nonce';
              }}};
              window.__ulgOperatorActivity = {};
              const ret = eval(js);
              return {ret, calls};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "probe nonce",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "html": empty_html,
                "writer": writer,
            },
        )
        assert empty_row["ret"]["ok"] is True
        assert empty_row["calls"] == 1

        foreign_row = page.evaluate(
            """({js, html}) => {
              document.body.innerHTML = '<form><div data-composer="1"></div></form>';
              const el = document.querySelector('[data-composer]');
              el.innerHTML = html;
              Object.defineProperty(el, 'innerText', {get() { return '  human note  '; }});
              let calls = 0;
              el.editor = {isEmpty: false, commands: {setContent(h) { calls += 1; el.innerHTML = h; }}};
              window.__ulgOperatorActivity = {};
              const ret = eval(js);
              return {ret, calls, inner: el.innerText};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "probe nonce",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "html": foreign_html,
            },
        )
        assert foreign_row["ret"]["reason"] == "composer_not_empty"
        assert foreign_row["calls"] == 0

        atom_row = page.evaluate(
            """({js, html}) => {
              document.body.innerHTML = '<form><div data-composer="1"></div></form>';
              const el = document.querySelector('[data-composer]');
              el.innerHTML = html;
              Object.defineProperty(el, 'innerText', {get() { return ''; }});
              let calls = 0;
              const doc = {
                textContent: '',
                descendants(fn) {
                  fn({isText: false, isAtom: true, type: {name: 'image'}});
                },
              };
              el.editor = {isEmpty: false, state: {doc}, commands: {setContent(h) { calls += 1; el.innerHTML = h; }}};
              window.__ulgOperatorActivity = {};
              return {ret: eval(js), calls};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "probe nonce",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "html": '<p class="is-editor-empty"><img src="x"></p>',
            },
        )
        assert atom_row["ret"]["reason"] == "composer_not_empty"
        assert atom_row["calls"] == 0

        stale_class_row = page.evaluate(
            """({js, html}) => {
              document.body.innerHTML = '<form><div data-composer="1"></div></form>';
              const el = document.querySelector('[data-composer]');
              el.innerHTML = html;
              Object.defineProperty(el, 'innerText', {get() { return 'foreign text'; }});
              let calls = 0;
              el.editor = {isEmpty: true, commands: {setContent(h) { calls += 1; el.innerHTML = h; }}};
              window.__ulgOperatorActivity = {};
              return {ret: eval(js), calls};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "probe nonce",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "html": (
                    '<p class="is-editor-empty is-empty">'
                    '<br class="ProseMirror-trailingBreak"></p>'
                ),
            },
        )
        assert stale_class_row["ret"]["reason"] == "composer_not_empty"
        assert stale_class_row["calls"] == 0

        literal_bs_n_row = page.evaluate(
            """({js, html}) => {
              document.body.innerHTML = '<form><div data-composer="1"></div></form>';
              const el = document.querySelector('[data-composer]');
              el.innerHTML = html;
              Object.defineProperty(el, 'innerText', {get() { return '\\\\n'; }});
              let calls = 0;
              el.editor = {
                isEmpty: true,
                state: {doc: {textContent: '\\\\n'}},
                commands: {setContent(h) { calls += 1; el.innerHTML = h; }},
              };
              window.__ulgOperatorActivity = {};
              return {ret: eval(js), calls};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "probe nonce",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "html": empty_html,
            },
        )
        assert literal_bs_n_row["ret"]["reason"] == "composer_not_empty"
        assert literal_bs_n_row["calls"] == 0

        browser.close()


def _old_paragraph_html(raw: str) -> str:
    """The previous per-line paragraph setter. Chromium turns adjacent p into a blank line."""
    esc = str(raw).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return "".join(f"<p>{part}</p>" for part in esc.split("\n"))


def test_chromium_editor_round_trips_literal_text_and_old_paragraphs_do_not() -> None:
    """Run the generated setter in headless Chromium and read real innerText.

    The old one-paragraph-per-line HTML is frozen here so this test fails that
    shape. This is not a live Grok or TipTap page.
    """
    from playwright.sync_api import sync_playwright

    writer = "interlocutor:agent"
    payloads = {
        "newline": "line1\nline2",
        "blank": "a\n\nb",
        "indent": "line\n  indented",
        "markup": "<b>literal</b>",
        "entity": "a &lt; b",
        "emoji": "hi \U0001f600",
    }

    def _page_eval(page, expression: str, arg: dict) -> dict:
        return page.evaluate(expression, arg)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        old_diverges = {"newline", "indent"}
        for name, line in payloads.items():
            old_text = _page_eval(
                page,
                """(html) => {
                  const probe = document.createElement('div');
                  probe.innerHTML = html;
                  document.body.appendChild(probe);
                  const text = probe.innerText;
                  probe.remove();
                  return text;
                }""",
                _old_paragraph_html(line),
            )
            if name in old_diverges:
                assert old_text != line, (name, old_text)
            if name == "newline":
                assert old_text == "line1\n\nline2"
            setter = _set_composer_body(
                "[data-composer]",
                line,
                preserve_newlines=True,
                draft_writer=writer,
            )
            row = _page_eval(
                page,
                """({js}) => {
                  document.body.innerHTML = '<form><div data-composer="1"></div></form>';
                  const el = document.querySelector('[data-composer]');
                  let calls = 0;
                  el.editor = {commands: {setContent(html) { calls += 1; el.innerHTML = html; }}};
                  window.__ulgOperatorActivity = {};
                  const ret = eval(js);
                  return {ret, inner: el.innerText, calls};
                }""",
                {"js": setter},
            )
            assert row["ret"]["ok"] is True, (name, row)
            assert row["calls"] == 1
            assert row["inner"] == line, (name, repr(row["inner"]))

        foreign = _page_eval(
            page,
            """({js}) => {
              document.body.innerHTML = '<form><div data-composer="1"></div></form>';
              const el = document.querySelector('[data-composer]');
              let calls = 0;
              el.editor = {commands: {setContent(html) { calls += 1; el.innerHTML = html; }}};
              el.textContent = 'human note';
              window.__ulgOperatorActivity = {};
              const ret = eval(js);
              return {ret, calls, inner: el.innerText};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                )
            },
        )
        assert foreign["ret"]["reason"] == "composer_not_empty"
        assert foreign["calls"] == 0
        assert foreign["inner"] == "human note"

        bare = _page_eval(
            page,
            """({js}) => {
              document.body.innerHTML = '<form><div data-composer="1">hello</div></form>';
              const el = document.querySelector('[data-composer]');
              let calls = 0;
              el.editor = {commands: {setContent(html) { calls += 1; el.innerHTML = html; }}};
              window.__ulgOperatorActivity = {};
              const ret = eval(js);
              return {ret, calls};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                )
            },
        )
        assert bare["ret"]["reason"] == "composer_not_empty"
        assert bare["calls"] == 0

        owned = _page_eval(
            page,
            """({js, writer}) => {
              document.body.innerHTML = '<form><div data-composer="1">hello</div></form>';
              const el = document.querySelector('[data-composer]');
              let calls = 0;
              el.editor = {commands: {setContent(html) { calls += 1; el.innerHTML = html; }}};
              window.__ulgOperatorActivity = {ourDraftWriter: writer, ourDraftSig: 'hello'};
              const ret = eval(js);
              return {ret, calls};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "writer": writer,
            },
        )
        assert owned["ret"].get("used_existing_draft") is True
        assert owned["calls"] == 0

        changed = _page_eval(
            page,
            """({js, writer}) => {
              document.body.innerHTML = '<form><div data-composer="1">hello edited</div></form>';
              const el = document.querySelector('[data-composer]');
              let calls = 0;
              el.editor = {commands: {setContent(html) { calls += 1; el.innerHTML = html; }}};
              window.__ulgOperatorActivity = {ourDraftWriter: writer, ourDraftSig: 'hello'};
              const ret = eval(js);
              return {ret, calls, inner: el.innerText};
            }""",
            {
                "js": _set_composer_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    draft_writer=writer,
                ),
                "writer": writer,
            },
        )
        assert changed["ret"]["reason"] == "composer_not_empty"
        assert changed["calls"] == 0
        assert changed["inner"] == "hello edited"

        mismatch = _page_eval(
            page,
            """async ({js}) => {
              document.body.innerHTML = '<form><div data-composer="1">other</div><button aria-label="Send" type="button">Send</button></form>';
              let clicks = 0;
              document.querySelector('button').addEventListener('click', () => { clicks += 1; });
              window.__ulgOperatorActivity = {};
              const ret = await eval(js);
              return {ret, clicks};
            }""",
            {
                "js": _click_send_body(
                    "[data-composer]", "hello", preserve_newlines=True
                )
            },
        )
        assert mismatch["ret"]["reason"] == "composer_text_mismatch"
        assert mismatch["clicks"] == 0

        matched = _page_eval(
            page,
            """async ({js}) => {
              document.body.innerHTML = '<form><div data-composer="1">hello</div><button aria-label="Send" type="button">Send</button></form>';
              let clicks = 0;
              document.querySelector('button').addEventListener('click', () => { clicks += 1; });
              window.__ulgOperatorActivity = {composerSendPermits: {n1: performance.now() + 60000}};
              const ret = await eval(js);
              return {ret, clicks};
            }""",
            {
                "js": _click_send_body(
                    "[data-composer]",
                    "hello",
                    preserve_newlines=True,
                    abort_nonce="n1",
                )
            },
        )
        assert matched["ret"]["ok"] is True
        assert matched["clicks"] == 1
        browser.close()


@pytest.mark.asyncio
async def test_send_timeout_keeps_clicked_unknown_and_inserted_phase(
    monkeypatch,
) -> None:
    async def fake_set(*_a, **_k):
        return {"ok": True, "inserted": True, "pasted": True, "phase": "inserted"}

    async def fake_click(*_a, **_k):
        return {
            "ok": False,
            "reason": "cdp_eval_timeout",
            "clicked_unknown": True,
            "retryable": False,
            "send_clicked": False,
        }

    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste.dom_set_composer_text", fake_set
    )
    monkeypatch.setattr("cursor_bridge.dom_composer_paste.dom_click_send", fake_click)
    out = await dom_insert_and_send(
        "ws://fixture",
        selector="s",
        line="hi",
        preserve_newlines=True,
        draft_writer="interlocutor:agent",
    )
    assert out["ok"] is False
    assert out["clicked_unknown"] is True
    assert out["pasted"] is True
    assert out["inserted"] is True
    assert out["phase"] == "send_unknown"
    assert out["retryable"] is False
