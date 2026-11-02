"""CDP Input.insertText must not leave operator_active from trusted input events."""

from cursor_bridge.cdp_composer_input import cdp_insert_text
from cursor_bridge.dom_composer_paste import (
    operator_activity_clear_suppress_js,
    operator_activity_suppress_js,
)


def test_cdp_insert_text_evaluates_suppress_clear_pair(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_eval(ws, mid, expression):  # noqa: ANN001
        calls.append(expression)
        if "no_composer" in expression:
            return {"ok": True, "priorIsComposer": True}
        return {"ok": True}

    async def fake_response(ws, mid):  # noqa: ANN001
        return {}

    class FakeWs:
        async def send(self, _payload: str) -> None:
            return None

        async def recv(self) -> str:
            return "{}"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):  # noqa: ANN001
            return None

    import websockets

    monkeypatch.setattr(websockets, "connect", lambda *a, **k: FakeWs())
    monkeypatch.setattr("cursor_bridge.cdp_composer_input._runtime_eval", fake_eval)
    monkeypatch.setattr("cursor_bridge.cdp_composer_input._cdp_response", fake_response)

    import asyncio

    out = asyncio.run(
        cdp_insert_text("ws://fake", selector="#composer", text="hi", restore_focus=False)
    )
    assert out["ok"] is True
    joined = "\n".join(calls)
    assert operator_activity_suppress_js().strip() in joined or "suppressUntilMs" in joined
    assert operator_activity_clear_suppress_js().strip() in joined or "suppressUntilMs=0" in joined
