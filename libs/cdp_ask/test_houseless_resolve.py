"""Unit tests for Jupiter provenance-backed houseless resolution."""

from __future__ import annotations

import pytest

from cdp_ask.client import CdpAskClientError
from cdp_ask.houseless_resolve import HouselessResolution, resolve_houseless_target

pytestmark = pytest.mark.offline

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
    assert res == HouselessResolution(ok=False, reason="provenance_unreachable")


def test_resolve_houseless_fresh_memo_bypasses_scan_cache() -> None:
    from cdp_ask.houseless_resolve import _RESOLVE_MEMO, clear_houseless_resolve_memo

    clear_houseless_resolve_memo()
    _RESOLVE_MEMO[_REG] = HouselessResolution(ok=False, reason="no_row")
    bad = resolve_houseless_target(_REG, client=_FakeClient(payload=_ok_payload()))
    assert bad.reason == "no_row"
    fresh = resolve_houseless_target(
        _REG, client=_FakeClient(payload=_ok_payload()), memo={}
    )
    assert fresh.ok is True


_UUID = "2762063d-2966-4859-a3ce-23831c6e0c86"
_UNIFIED = f"https://claude.ai/chat/{_UUID}"
_REG_HOUSELESS = "reg-houseless-unified"


class _RegistryProvenanceClient:
    """HTTP client stub that mirrors post-bind Jupiter provenance fields."""

    def __init__(self, registration_id: str) -> None:
        self.registration_id = registration_id

    def _request(self, method: str, path: str) -> dict:
        from claude_bundles import cdp_registry_store as store

        row = store.load_active().get(self.registration_id)
        if not isinstance(row, dict):
            return {}
        chat_url = str(row.get("chat_url") or "").strip()
        return {
            "registration_id": self.registration_id,
            "purpose": row.get("purpose"),
            "status": row.get("status"),
            "seat_open": bool(row.get("seat_open")),
            "chat_url": chat_url,
        }


def test_bind_unified_chat_resolves_houseless_unmocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import json

    from claude_bundles import cdp_registry as reg
    from claude_bundles import cdp_registry_store as store
    from claude_bundles import cdp_registry_store as store_mod
    from claude_bundles import cse_provenance
    from claude_bundles.cdp_registry import session_address

    from services.git_integration_worker import cse_session_holders
    from services.git_integration_worker.cse_session_holders import (
        RemoteUpsert,
        ensure_schema,
        upsert_holder,
    )
    from services.git_integration_worker.cursor_dispatch_ledger import (
        CursorDispatchLedger,
    )

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    paths = {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }
    for name, value in paths.items():
        monkeypatch.setattr(store, name, value)
        monkeypatch.setattr(cse_provenance.store, name, value)
        monkeypatch.setattr(store_mod, name, value)
    store.ensure_dirs()
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: True)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "holder-data"))
    monkeypatch.setattr(CursorDispatchLedger, "_instance", None)
    ledger = CursorDispatchLedger.instance()
    with ledger._connect() as conn:
        ensure_schema(conn)

    holder_calls: list[dict[str, object]] = []

    def _counting_holder_upsert(**kw: object) -> RemoteUpsert:
        holder_calls.append(dict(kw))
        with ledger._connect() as conn:
            ensure_schema(conn)
            upsert_holder(conn, **dict(kw))  # type: ignore[arg-type]
        return RemoteUpsert.OK

    monkeypatch.setattr(
        cse_session_holders,
        "upsert_holder_remote",
        _counting_holder_upsert,
    )
    store.write_active(
        {
            _REG_HOUSELESS: {
                "registration_id": _REG_HOUSELESS,
                "port": 9233,
                "status": "active",
                "purpose": "houseless",
                "seat_open": False,
                "chat_url": "",
            }
        }
    )
    assert session_address.bind_session_address(_REG_HOUSELESS, chat_url=_UNIFIED)
    row = store.load_active()[_REG_HOUSELESS]
    assert row["chat_url"] == _UNIFIED
    episode = cse_provenance.latest_episode(registration_id=_REG_HOUSELESS)
    assert episode is not None
    assert episode.chat_url == _UNIFIED
    res = resolve_houseless_target(
        _REG_HOUSELESS,
        client=_RegistryProvenanceClient(_REG_HOUSELESS),
    )
    assert res.ok is True
    assert res.chat_url == _UNIFIED
    assert res.reason != "no_chat_url"

    bound_logs = [
        json.loads(line)
        for line in store.REGISTRY_LOG.read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("event") == "session_address_bound"
        and json.loads(line).get("registration_id") == _REG_HOUSELESS
    ]
    assert len(bound_logs) == 1
    assert bound_logs[0].get("holder_projection") == "not_applicable"
    assert holder_calls == []
    with ledger._connect() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM cse_session_holders"
        ).fetchone()["n"]
    assert count == 0


def test_resolve_houseless_tab_seat_ok() -> None:
    res = resolve_houseless_target(
        _REG, client=_FakeClient(payload=_ok_payload(status="tab"))
    )
    assert res.ok is True
    assert getattr(res, "tab", None) is True
    assert res.chat_url == _CHAT


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"chat_url": ""}, "no_chat_url"),
        ({"seat_open": True}, "seat_open"),
    ],
)
def test_resolve_houseless_tab_fail_closed(
    overrides: dict[str, object], reason: str
) -> None:
    res = resolve_houseless_target(
        _REG, client=_FakeClient(payload=_ok_payload(status="tab", **overrides))
    )
    assert res.ok is False
    assert res.reason == reason


def test_resolve_houseless_memoizes_per_registration() -> None:
    from cdp_ask.houseless_resolve import clear_houseless_resolve_memo

    clear_houseless_resolve_memo()
    client = _FakeClient(payload=_ok_payload())
    first = resolve_houseless_target(_REG, client=client)
    second = resolve_houseless_target(_REG, client=client)
    assert first.ok and second.ok
    assert client.request_count == 1
