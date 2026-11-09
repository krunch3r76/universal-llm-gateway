"""Unit tests for Jupiter provenance-backed houseless resolution."""

from __future__ import annotations

import pytest

from cdp_ask.client import CdpAskClientError
from cdp_ask.houseless_resolve import HouselessResolution, resolve_houseless_target

pytestmark = pytest.mark.offline


@pytest.fixture(autouse=True)
def _clear_houseless_memo() -> None:
    from cdp_ask.houseless_resolve import clear_houseless_resolve_memo

    clear_houseless_resolve_memo()
    yield
    clear_houseless_resolve_memo()


_REG = "583b411df34247f9a48bc044b6518be3"
_CHAT = "https://claude.ai/cowork/cse_houseless_mailbox"


class _FakeClient:
    def __init__(
        self,
        *,
        payload: dict | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self._payload = payload
        self._exc = exc
        self.request_count = 0

    def _request(self, method: str, path: str) -> dict:
        del method, path
        self.request_count += 1
        if self._exc is not None:
            raise self._exc
        assert self._payload is not None
        return dict(self._payload)


def _ok_payload(**overrides: object) -> dict:
    base = {
        "registration_id": _REG,
        "purpose": "houseless",
        "status": "active",
        "seat_open": False,
        "chat_url": _CHAT,
    }
    base.update(overrides)
    return base


def test_resolve_houseless_ok() -> None:
    res = resolve_houseless_target(_REG, client=_FakeClient(payload=_ok_payload()))
    assert res == HouselessResolution(ok=True, reason="ok", chat_url=_CHAT)


class _NonDictClient:
    def _request(self, method: str, path: str) -> object:
        del method, path
        return None


@pytest.mark.parametrize(
    "client,reason",
    [
        (_NonDictClient(), "no_row"),
        (_FakeClient(payload=_ok_payload(registration_id="other")), "no_row"),
        (
            _FakeClient(payload=_ok_payload(purpose="operator-proxy")),
            "purpose_not_houseless",
        ),
        (
            _FakeClient(
                payload={k: v for k, v in _ok_payload().items() if k != "purpose"}
            ),
            "purpose_not_houseless",
        ),
        (_FakeClient(payload=_ok_payload(status="dormant")), "status_unlisted"),
        (_FakeClient(payload=_ok_payload(seat_open=True)), "seat_open"),
        (_FakeClient(payload=_ok_payload(chat_url="")), "no_chat_url"),
    ],
)
def test_resolve_houseless_fail_closed(
    client: _FakeClient | _NonDictClient, reason: str
) -> None:
    res = resolve_houseless_target(_REG, client=client)
    assert res.ok is False
    assert res.reason == reason


def test_resolve_houseless_cdp_ask_unreachable() -> None:
    res = resolve_houseless_target(
        _REG,
        client=_FakeClient(exc=CdpAskClientError("down")),
    )
    assert res == HouselessResolution(ok=False, reason="no_row")


def test_resolve_houseless_memoizes_per_registration() -> None:
    from cdp_ask.houseless_resolve import clear_houseless_resolve_memo

    clear_houseless_resolve_memo()
    client = _FakeClient(payload=_ok_payload())
    first = resolve_houseless_target(_REG, client=client)
    second = resolve_houseless_target(_REG, client=client)
    assert first.ok and second.ok
    assert client.request_count == 1
