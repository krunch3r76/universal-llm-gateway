"""Bounded send-button wait and no_send_button census (_click_send_body)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from cursor_bridge.dom_composer_paste import (
    SEND_BTN_CDP_EVAL_TIMEOUT_S,
    SEND_BTN_POLL_INTERVAL_MS,
    SEND_BTN_WAIT_CEILING_MS,
    _click_send_body,
    dom_insert_and_send,
)


def _run_click_send_js(
    js: str,
    *,
    composer_text: str,
    send_appears_after_searches: int | None = 1,
    send_appears_after_ms: int | None = None,
    buttons: list[dict[str, Any]] | None = None,
    async_js: bool = True,
) -> dict[str, Any]:
    """Evaluate generated click-send JS in Node with a minimal DOM mock."""
    if send_appears_after_searches is None:
        appear_after = -1
    else:
        appear_after = int(send_appears_after_searches)
    appear_ms = "null" if send_appears_after_ms is None else int(send_appears_after_ms)
    btn_spec = json.dumps(buttons or [])
    harness = f"""
const appearAfter = {appear_after};
const appearAfterMs = {appear_ms};
const composerText = {json.dumps(composer_text)};
const extraButtons = {btn_spec};
let sendSearchCount = 0;
let sendVisibleByTimer = appearAfterMs === null;

class MockEl {{
  constructor(tag, opts = {{}}) {{
    this.tagName = tag.toUpperCase();
    this.innerText = opts.innerText ?? '';
    this.disabled = opts.disabled ?? false;
    this._attrs = opts.attrs ?? {{}};
    this._rect = opts.rect ?? {{ width: 10, height: 10 }};
    this._parent = opts.parent ?? null;
    this._form = opts.form ?? null;
    this._children = opts.children ?? [];
    this.clicked = false;
  }}
  getAttribute(name) {{
    return this._attrs[name] ?? null;
  }}
  hasAttribute(name) {{
    return Object.prototype.hasOwnProperty.call(this._attrs, name);
  }}
  getBoundingClientRect() {{
    return this._rect;
  }}
  closest(sel) {{
    if (sel === 'form') return this._form;
    return null;
  }}
  get parentElement() {{
    return this._parent;
  }}
  querySelector(sel) {{
    const isSendSel =
      sel.includes('Send') || sel.includes('send-button') || sel.includes('submit');
    if (isSendSel) {{
      sendSearchCount += 1;
      if (appearAfterMs !== null) {{
        return sendVisibleByTimer ? this._sendBtn : null;
      }}
      if (appearAfter < 0 || sendSearchCount < appearAfter) return null;
      return this._sendBtn;
    }}
    return null;
  }}
  querySelectorAll(sel) {{
    if (sel === 'button,[role=button]') {{
      return [...this._children];
    }}
    return [];
  }}
  get outerHTML() {{
    return '<form mock="1"></form>';
  }}
  click() {{
    this.clicked = true;
  }}
}}

const sendBtn = new MockEl('button', {{
  attrs: {{ 'aria-label': 'Send', type: 'submit' }},
}});
const form = new MockEl('form', {{ children: extraButtons.map((b) => new MockEl('button', {{
  attrs: b,
  rect: b.non_zero_rect === false ? {{ width: 0, height: 0 }} : {{ width: 10, height: 10 }},
}})) }});
form._sendBtn = sendBtn;
const composer = new MockEl('div', {{
  innerText: composerText,
  form,
  parent: form,
}});
form.querySelector = form.querySelector.bind(form);
form._sendBtn = sendBtn;

global.document = {{
  visibilityState: 'visible',
  hasFocus: () => true,
  querySelector: (sel) => (sel.includes('Prompt') || sel.includes('contenteditable') ? composer : null),
}};
global.window = {{ __ulgOperatorActivity: {{}} }};
global.HTMLTextAreaElement = {{ prototype: {{}} }};
global.HTMLInputElement = {{ prototype: {{}} }};

if (appearAfterMs !== null) {{
  setTimeout(() => {{ sendVisibleByTimer = true; }}, appearAfterMs);
}}

(async () => {{
  const result = {"await " + js if async_js else js};
  result.__sendSearchCount = sendSearchCount;
  console.log(JSON.stringify(result));
}})().catch((err) => {{
  console.error(err);
  process.exit(1);
}});
"""
    proc = subprocess.run(
        ["node", "-e", harness],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    out = json.loads(proc.stdout.strip())
    return out


def test_send_control_found_immediately_no_extra_poll() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(js, composer_text="hello", send_appears_after_searches=1)
    assert result["ok"] is True
    assert result["send_clicked"] is True
    assert result["__sendSearchCount"] == 1


def test_send_control_appears_on_third_search() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "wake text")
    result = _run_click_send_js(
        js, composer_text="wake text", send_appears_after_searches=3
    )
    assert result["ok"] is True
    assert result["send_clicked"] is True
    assert result["__sendSearchCount"] == 3


def test_send_never_appears_census_and_bounded_wait() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=None,
        buttons=[
            {"aria-label": "Dictate", "type": "button"},
            {"aria-label": "Send", "type": "submit", "disabled": "true"},
        ],
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"
    census = result["census"]
    assert census["root_tag"] == "form"
    assert "root_outer_html" in census
    assert len(census["root_outer_html"]) <= 4000
    assert census["document_visibility_state"] == "visible"
    assert census["document_has_focus"] is True
    assert isinstance(census["buttons"], list)
    assert census["elapsed_ms"] <= SEND_BTN_WAIT_CEILING_MS + SEND_BTN_POLL_INTERVAL_MS + 50


def test_dom_insert_and_send_inserts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    insert_calls = 0
    send_calls = 0

    async def fake_eval(_ws: str, expr: str, **_kw: Any) -> Any:
        nonlocal insert_calls, send_calls
        if "setContent" in expr or "textContent=line" in expr:
            insert_calls += 1
            return {"ok": True, "sig": "hello"}
        send_calls += 1
        return {"ok": True, "send_clicked": True}

    monkeypatch.setattr(
        "cursor_bridge.dom_composer_paste._cdp_eval",
        fake_eval,
    )
    import asyncio

    out = asyncio.run(
        dom_insert_and_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    )
    assert out["ok"] is True
    assert insert_calls == 1
    assert send_calls == 1


@pytest.mark.asyncio
async def test_dom_click_send_logs_census_on_no_send_button(
    caplog: pytest.LogCaptureFixture,
) -> None:
    census = {"root_tag": "form", "buttons": [], "elapsed_ms": 3000}
    with patch(
        "cursor_bridge.dom_composer_paste._cdp_eval",
        new=AsyncMock(
            return_value={
                "ok": False,
                "reason": "no_send_button",
                "send_clicked": False,
                "census": census,
            }
        ),
    ):
        from cursor_bridge.dom_composer_paste import dom_click_send

        await dom_click_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    assert any("no_send_button census=" in r.message for r in caplog.records)


def test_click_send_js_has_no_sync_date_spin() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    assert "while(Date.now()-" not in js.replace(" ", "")
    assert "sleepMs" not in js


def test_cdp_eval_timeout_exceeds_send_wait_ceiling() -> None:
    assert SEND_BTN_CDP_EVAL_TIMEOUT_S > SEND_BTN_WAIT_CEILING_MS / 1000.0


def test_send_control_appears_after_timer_yield() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_ms=300,
    )
    assert result["ok"] is True
    assert result["send_clicked"] is True


def _legacy_click_send_body_at(rev: str) -> Any:
    proc = subprocess.run(
        ["git", "show", f"{rev}:libs/cursor_bridge/dom_composer_paste.py"],
        capture_output=True,
        text=True,
        check=True,
        cwd="/mnt/torus/projects/ulg-arc-worktrees/universal-llm-gateway/lane-21979",
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(proc.stdout)
        path = Path(tmp.name)
    spec = importlib.util.spec_from_file_location(f"legacy_dcp_{rev[:8]}", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    path.unlink(missing_ok=True)
    return mod._click_send_body


def test_legacy_spin_body_fails_timer_appearance() -> None:
    """838d2c18 sync spin blocks Node timers — same failure mode as a real page."""
    legacy_fn = _legacy_click_send_body_at("838d2c18")
    legacy_js = legacy_fn('[aria-label="Prompt"][contenteditable="true"]', "hello")
    result = _run_click_send_js(
        legacy_js,
        composer_text="hello",
        send_appears_after_ms=300,
        async_js=False,
    )
    assert result["ok"] is False
    assert result["reason"] == "no_send_button"


def test_census_buttons_capped_at_twenty() -> None:
    js = _click_send_body('[aria-label="Prompt"][contenteditable="true"]', "hello")
    many = [{"aria-label": f"b{i}", "type": "button"} for i in range(50)]
    result = _run_click_send_js(
        js,
        composer_text="hello",
        send_appears_after_searches=None,
        buttons=many,
    )
    census = result["census"]
    assert len(census["buttons"]) == 20
    assert census["buttons_total"] == 50


@pytest.mark.asyncio
async def test_dom_insert_and_send_forwards_census_on_send_failure() -> None:
    census = {"root_tag": "form", "buttons": [], "buttons_total": 0, "elapsed_ms": 3000}
    call_idx = 0

    async def fake_eval(_ws: str, expr: str, **_kw: Any) -> Any:
        nonlocal call_idx
        call_idx += 1
        if call_idx == 1:
            return {"ok": True, "sig": "hello"}
        return {
            "ok": False,
            "reason": "no_send_button",
            "send_clicked": False,
            "census": census,
        }

    with patch("cursor_bridge.dom_composer_paste._cdp_eval", new=fake_eval):
        from cursor_bridge.dom_composer_paste import dom_insert_and_send

        out = await dom_insert_and_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    assert out["ok"] is False
    assert out["reason"] == "no_send_button"
    assert out["census"] == census


@pytest.mark.asyncio
async def test_dom_click_send_uses_await_promise() -> None:
    with patch(
        "cursor_bridge.dom_composer_paste._cdp_eval",
        new=AsyncMock(return_value={"ok": True, "send_clicked": True}),
    ) as mock_eval:
        from cursor_bridge.dom_composer_paste import dom_click_send

        await dom_click_send(
            "ws://x",
            selector='[aria-label="Prompt"][contenteditable="true"]',
            line="hello",
        )
    assert mock_eval.await_args.kwargs.get("await_promise") is True
