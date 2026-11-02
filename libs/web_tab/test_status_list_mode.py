"""List-mode web-tab status index (read-only, bounded)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from web_tab.cdp_port import RecordingCdpPort
from web_tab.status_report import build_status_list_report


@dataclass
class _CountingCdpPort(RecordingCdpPort):
    list_calls: list[str] = field(default_factory=list)

    def list_tabs(self, *, cdp_url: str) -> dict:
        self.list_calls.append(cdp_url)
        return super().list_tabs(cdp_url=cdp_url)


@pytest.mark.asyncio
@pytest.mark.offline
async def test_list_mode_fixture_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    pairs = [("21342", "orion"), ("21352", "orion")]

    def _enum() -> list[tuple[str, str]]:
        return list(pairs)

    def _scope(house_id: str, agent: str) -> dict:
        if house_id == "21342":
            return {
                "binding_state": "bound",
                "url": "https://chatgpt.com/c/abc",
                "conversation_id": "abc",
                "binding_row_id": 3454,
                "cdp_url": "http://127.0.0.1:9342",
            }
        return {
            "binding_state": "unbound",
            "url": None,
            "conversation_id": None,
            "binding_row_id": None,
            "cdp_url": None,
        }

    def _wake(house_id: str, agent: str) -> dict:
        if house_id == "21342":
            return {"state": "unbound"}
        return {"state": "bound", "current": {"actuation": {"conversation_id": "xyz"}}}

    def _attest(house_id: str, agent: str) -> dict:
        return {
            "available": True,
            "turn": {
                "turn_number": 339 if house_id == "21342" else 1,
                "created_at": "2026-11-01T18:47:51Z",
            },
        }

    def _ledger(house_id: str, agent: str):
        if house_id == "21342":
            delivered = {
                "wake_id": "w-del",
                "reason": 'paste_receipt:{"conversation_id":"abc","delivered":true}',
                "created_at": "2026-10-31 02:41:00",
            }
            refused = {
                "state": "refused",
                "reason": "conversation_missing",
                "created_at": "2026-11-01 18:31:00",
            }
            return delivered, refused
        return None, None

    monkeypatch.setattr(
        "web_tab.status_report._read_agent_scope_binding",
        _scope,
    )
    monkeypatch.setattr(
        "web_tab.status_report._wake_ledger_snapshots",
        _ledger,
    )
    port = _CountingCdpPort(
        tabs=[{"id": "t1", "url": "https://chatgpt.com/c/abc", "title": ""}],
        reachable=True,
    )
    report = await build_status_list_report(
        pair_enumerator=_enum,
        binding_resolver=_wake,
        attestation_reader=_attest,
        port=port,
    )
    assert report["ok"] is True
    assert report["plane"] == "index"
    assert report["total"] == 2
    assert report["truncated"] is False
    row = next(r for r in report["rows"] if r["house_id"] == "21342")
    assert row["agent_scope_binding_state"] == "bound"
    assert row["wake_resolve_state"] == "unbound"
    assert row["binding_row_id"] == 3454
    assert row["surface"] == "chatgpt"
    assert row["newest_delivered_wake"]["conversation_id"] == "abc"
    assert row["newest_non_delivered_wake"]["state"] == "refused"
    assert row["last_attested_turn"] == 339
    assert row["tab_open"] is True


@pytest.mark.asyncio
@pytest.mark.offline
async def test_list_mode_surface_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    def _enum() -> list[tuple[str, str]]:
        return [("1", "orion"), ("2", "orion")]

    def _scope(house_id: str, agent: str) -> dict:
        if house_id == "1":
            return {
                "binding_state": "bound",
                "url": "https://chatgpt.com/c/a",
                "conversation_id": "a",
                "binding_row_id": 1,
                "cdp_url": "http://127.0.0.1:9342",
            }
        return {
            "binding_state": "unbound",
            "url": "https://claude.ai/chat/xyz",
            "conversation_id": None,
            "binding_row_id": 2,
            "cdp_url": "http://127.0.0.1:9343",
        }

    monkeypatch.setattr("web_tab.status_report._read_agent_scope_binding", _scope)
    monkeypatch.setattr(
        "web_tab.status_report._wake_ledger_snapshots", lambda h, a: (None, None)
    )
    port = RecordingCdpPort(reachable=True)
    report = await build_status_list_report(
        pair_enumerator=_enum,
        surface_filter="chatgpt",
        port=port,
        binding_resolver=lambda h, a: None,
        attestation_reader=lambda h, a: {"available": False, "turn": None},
    )
    assert [r["house_id"] for r in report["rows"]] == ["1"]


@pytest.mark.asyncio
@pytest.mark.offline
async def test_list_mode_cdp_unreachable_tab_open_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "web_tab.status_report.enumerate_house_agent_tab_index_pairs",
        lambda: [("99", "orion")],
    )
    monkeypatch.setattr(
        "web_tab.status_report._read_agent_scope_binding",
        lambda h, a: {
            "binding_state": "bound",
            "url": "https://chatgpt.com/c/open-me",
            "conversation_id": "open-me",
            "binding_row_id": 9,
            "cdp_url": "http://127.0.0.1:9342",
        },
    )
    monkeypatch.setattr(
        "web_tab.status_report._wake_ledger_snapshots",
        lambda h, a: (None, None),
    )
    port = RecordingCdpPort(reachable=False, list_error="URLError")
    report = await build_status_list_report(
        port=port,
        binding_resolver=lambda h, a: None,
        attestation_reader=lambda h, a: {"available": False, "turn": None},
    )
    assert report["rows"][0]["tab_open"] is None


@pytest.mark.asyncio
@pytest.mark.offline
async def test_list_mode_row_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    pairs = [(str(i), "orion") for i in range(55)]

    monkeypatch.setattr(
        "web_tab.status_report._read_agent_scope_binding",
        lambda h, a: {
            "binding_state": "unbound",
            "url": None,
            "conversation_id": None,
            "binding_row_id": None,
            "cdp_url": "http://127.0.0.1:9342",
        },
    )
    monkeypatch.setattr(
        "web_tab.status_report._wake_ledger_snapshots",
        lambda h, a: (None, None),
    )
    port = RecordingCdpPort(reachable=True)
    report = await build_status_list_report(
        pair_enumerator=lambda: pairs,
        port=port,
        row_cap=50,
        binding_resolver=lambda h, a: None,
        attestation_reader=lambda h, a: {"available": False, "turn": None},
    )
    assert report["total"] == 55
    assert report["count"] == 50
    assert report["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.offline
async def test_list_mode_one_cdp_list_per_distinct_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _enum() -> list[tuple[str, str]]:
        return [("1", "a"), ("2", "b"), ("3", "c")]

    def _scope(house_id: str, agent: str) -> dict:
        cdp = "http://127.0.0.1:9342" if house_id != "3" else "http://127.0.0.1:9999"
        return {
            "binding_state": "bound",
            "url": f"https://chatgpt.com/c/{house_id}",
            "conversation_id": house_id,
            "binding_row_id": int(house_id),
            "cdp_url": cdp,
        }

    monkeypatch.setattr("web_tab.status_report._read_agent_scope_binding", _scope)
    monkeypatch.setattr(
        "web_tab.status_report._wake_ledger_snapshots", lambda h, a: (None, None)
    )
    port = _CountingCdpPort(reachable=True)
    await build_status_list_report(
        pair_enumerator=_enum,
        port=port,
        binding_resolver=lambda h, a: None,
        attestation_reader=lambda h, a: {"available": False, "turn": None},
    )
    assert sorted(set(port.list_calls)) == [
        "http://127.0.0.1:9342",
        "http://127.0.0.1:9999",
    ]
    assert len(port.list_calls) == 2
