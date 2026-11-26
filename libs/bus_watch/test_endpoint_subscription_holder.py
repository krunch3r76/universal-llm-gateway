"""Holder join, actuation, and transfer retirement — plan AC1–AC10."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bus_watch import endpoint_subscription_holder as holder
from bus_watch.endpoint_wake_dispatcher import qualify

_UUID_A = "f19ca60c-27d7-43b0-bf7e-4e7d29a9eb03"
_UUID_B = "06e4b108-1ca1-4593-a172-37b0a40f39e4"
_UUID_C = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_UUID_D = "11111111-2222-3333-4444-555555555555"


def _sub_row(
    *,
    sub_id: int,
    scope: str = "24600",
    agent: str = "brg-grand-impresario",
    agent_id: str,
) -> dict[str, Any]:
    return {
        "id": sub_id,
        "agent": agent,
        "surface": "cursor",
        "thread_scope": scope,
        "display": "8",
        "state": "subscribed",
        "agent_id": agent_id,
        "title": f"tab-{agent_id[:8]}",
    }


def _claim(scope: str, tab_url: str) -> dict[str, Any]:
    return {
        "kind": "claim",
        "status": "claimed",
        "thread_id": scope,
        "expires_at": "2099-01-01T00:00:00Z",
        "address": {"tab_url": tab_url},
    }


def _registry(*entries: dict[str, Any], refusal: str | None = None) -> holder.RegistryClaims:
    return holder.RegistryClaims(entries, refusal)


def test_enumerates_all_display8_uuid_rows() -> None:
    rows = [
        _sub_row(sub_id=1, scope="24600", agent="brg-grand-impresario", agent_id=_UUID_A),
        _sub_row(sub_id=2, scope="25291", agent="cursor-houseless", agent_id=_UUID_B),
        _sub_row(sub_id=3, scope="25279", agent="cursor-gi", agent_id=_UUID_C),
        _sub_row(sub_id=4, scope="99999", agent="cursor-extra-mailbox", agent_id=_UUID_D),
        {
            "id": 5,
            "agent": "cursor",
            "surface": "cursor",
            "thread_scope": "24600",
            "display": "8",
            "state": "subscribed",
            "agent_id": "not-a-uuid",
        },
    ]
    got = holder.enumerate_display8_uuid_rows(rows)
    assert len(got) == 4
    assert {holder.is_uuid_agent_id(r["agent_id"]) for r in got} == {
        _UUID_A,
        _UUID_B,
        _UUID_C,
        _UUID_D,
    }


@pytest.mark.parametrize(
    ("tab_url", "expected"),
    [
        ("cursor://tab/f19ca60c-27d7-43b0-bf7e-4e7d29a9eb03", _UUID_A),
        ("cursor://agent/06e4b108-1ca1-4593-a172-37b0a40f39e4", _UUID_B),
        ("https://example.com", None),
    ],
)
def test_parse_tab_uuid_tab_and_agent(tab_url: str, expected: str | None) -> None:
    assert holder.parse_tab_uuid(tab_url) == expected


@pytest.mark.parametrize(
    "code,rows,claims,probe",
    [
        (
            "unknown",
            [],
            (),
            "glass.cdp",
        ),
        (
            "no_live_claim",
            [_sub_row(sub_id=1, agent_id=_UUID_A)],
            (),
            "glass.cdp",
        ),
        (
            "stale",
            [_sub_row(sub_id=1, agent_id=_UUID_A)],
            (_claim("24600", f"cursor://tab/{_UUID_B}"),),
            "glass.cdp",
        ),
        (
            "ambiguous",
            [
                _sub_row(sub_id=1, agent_id=_UUID_A),
                _sub_row(sub_id=2, agent_id=_UUID_B),
            ],
            (
                _claim("24600", f"cursor://tab/{_UUID_A}"),
                _claim("24600", f"cursor://agent/{_UUID_B}"),
            ),
            "glass.cdp",
        ),
        (
            "incomplete",
            [_sub_row(sub_id=1, agent_id=_UUID_A)],
            (_claim("24600", f"cursor://tab/{_UUID_A}"),),
            None,
        ),
        (
            "probe_not_glass_cdp",
            [_sub_row(sub_id=1, agent_id=_UUID_A)],
            (_claim("24600", f"cursor://tab/{_UUID_A}"),),
            "transcript_estimate",
        ),
        (
            "eligible",
            [_sub_row(sub_id=1, agent_id=_UUID_A)],
            (_claim("24600", f"cursor://tab/{_UUID_A}"),),
            "glass.cdp",
        ),
    ],
)
def test_holder_predicates(
    code: str,
    rows: list[dict[str, Any]],
    claims: tuple[dict[str, Any], ...],
    probe: str | None,
) -> None:
    res = holder.resolve_holder("24600", rows, _registry(*claims), budget_probe=probe)
    if code == "eligible":
        assert res.actuation_refused is None
        assert res.actuation_eligible is True
        assert res.holder_uuid == _UUID_A
    else:
        assert res.actuation_refused == code
        assert res.actuation_eligible is False


def test_claim_source_truncated_refuses() -> None:
    res = holder.resolve_holder(
        "24600",
        [_sub_row(sub_id=1, agent_id=_UUID_A)],
        _registry(_claim("24600", f"cursor://tab/{_UUID_A}"), refusal="claim_source_truncated"),
        budget_probe="glass.cdp",
    )
    assert res.actuation_refused == "claim_source_truncated"


def test_claim_source_unavailable_refuses() -> None:
    res = holder.resolve_holder(
        "24600",
        [_sub_row(sub_id=1, agent_id=_UUID_A)],
        _registry(refusal="claim_source_unavailable"),
        budget_probe="glass.cdp",
    )
    assert res.actuation_refused == "claim_source_unavailable"


def test_child_row_does_not_stale_parent() -> None:
    parent = _sub_row(sub_id=10, scope="24600", agent_id=_UUID_A)
    child = _sub_row(sub_id=20, scope="24600", agent="cursor-ladder", agent_id=_UUID_B)
    rows = [parent, child]
    claims = (_claim("24600", f"cursor://tab/{_UUID_A}"),)
    assert holder.scope_claim_stale(holder.claim_tab_uuids(list(claims)), rows) is False
    res = holder.resolve_holder("24600", rows, _registry(*claims), budget_probe="glass.cdp")
    assert res.actuation_refused is None
    assert res.holder_uuid == _UUID_A


def test_prepare_note_once_per_holder(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setattr(holder, "WATCH_DIR", watch)
    store = MagicMock()
    row = _sub_row(sub_id=1, agent_id=_UUID_A)
    assert holder.maybe_post_prepare_note(
        store,
        scope="24600",
        holder_row=row,
        prepare_advisory={"headroom_tokens": 1},
    )
    assert holder.maybe_post_prepare_note(
        store,
        scope="24600",
        holder_row=row,
        prepare_advisory={"headroom_tokens": 1},
    ) is False
    assert store.create_turn.call_count == 1
    assert store.create_turn.call_args.kwargs["subject"].startswith("NOTE|")


def test_request_subject_exact_and_clean() -> None:
    subject = holder.build_request_subject()
    assert subject == "REQUEST| precompaction continuity"
    assert holder.request_subject_is_clean(subject)


def test_qualify_admits_canonical_request_subject() -> None:
    turn = {
        "from_agent": "orion",
        "to_agent": "brg-grand-impresario",
        "subject": holder.build_request_subject(),
        "body": holder.hop_actuation_key("24600", _UUID_A, "hop"),
    }
    verdict = qualify(turn, cse=False)
    assert verdict.wake is True
    assert verdict.rule_id != "default_suppress"


def test_wake_ledger_delivered_for_holder() -> None:
    store = MagicMock()
    store._bus.return_value = {
        "rows": [{"state": "delivered", "turn_id": 99}],
    }
    assert holder.wake_ledger_delivered_for_turn(store, scope="24600", turn_id=99)


def test_request_idempotent_across_restart(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setattr(holder, "WATCH_DIR", watch)
    store = MagicMock()
    row = _sub_row(sub_id=1, agent_id=_UUID_A)
    first = holder.maybe_post_hop_request(
        store,
        scope="24600",
        holder_row=row,
        band="hop",
        policy_enabled=True,
        glass_generating=False,
        as_of="2026-11-26T00:00:00Z",
        used_tokens=150_000,
        window_limit_tokens=256_000,
    )
    second = holder.maybe_post_hop_request(
        store,
        scope="24600",
        holder_row=row,
        band="hop",
        policy_enabled=True,
        glass_generating=False,
        as_of="2026-11-26T00:00:01Z",
        used_tokens=151_000,
        window_limit_tokens=256_000,
    )
    assert first is not None
    assert second is None
    assert store.create_turn.call_count == 1


def test_actuation_policy_default_off() -> None:
    policy = {"ide_window_tokens": 256_000}
    assert holder.hop_actuation_policy_enabled(policy) is False
    assert holder.brg_actuation_allowed("25291", policy) is False
    assert holder.brg_actuation_allowed("24600", policy) is False


@pytest.mark.parametrize(
    "t1,t2,t3,t4,expected",
    [
        (True, True, True, True, True),
        (False, True, True, True, False),
        (True, False, True, True, False),
        (True, True, False, True, False),
        (True, True, True, False, False),
    ],
)
def test_transfer_incomplete_matrix(
    t1: bool, t2: bool, t3: bool, t4: bool, expected: bool
) -> None:
    rows = [
        _sub_row(sub_id=5, agent_id=_UUID_A),
        _sub_row(sub_id=30, agent_id=_UUID_B),
    ]
    claims: list[dict[str, Any]] = []
    if t2:
        claims = [_claim("24600", f"cursor://tab/{_UUID_B}")]
    turns = [
        {"subject": "ACK| hop landed", "from_agent_id": _UUID_B},
    ] if t3 else []
    ledger_rows: list[dict[str, Any]] = []
    if not t4:
        ledger_rows = [{"state": "pending", "agent_id": _UUID_A}]
    ctx = holder.TransferContext(
        scope="24600",
        mailbox_agent="brg-grand-impresario",
        old_uuid=_UUID_A,
        new_uuid=_UUID_B,
        request_row_id=5 if t1 else 30,
        rows=rows if t1 else [_sub_row(sub_id=5, agent_id=_UUID_A)],
        claims=claims,
        turns=turns,
        ledger_rows=ledger_rows,
    )
    if not t1:
        ctx = holder.TransferContext(
            scope=ctx.scope,
            mailbox_agent=ctx.mailbox_agent,
            old_uuid=ctx.old_uuid,
            new_uuid=ctx.new_uuid,
            request_row_id=30,
            rows=[_sub_row(sub_id=5, agent_id=_UUID_A)],
            claims=claims,
            turns=turns,
            ledger_rows=ledger_rows,
        )
    assert holder.is_transfer_complete(ctx) is expected


def test_suffix_retired_emits_ide_hop_transfer_accepted(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setattr(holder, "WATCH_DIR", watch)
    emitted: list[dict[str, Any]] = []

    def _emit(**kw: Any) -> None:
        emitted.append(kw)

    holder.execute_transfer_retirement(
        scope="24600",
        old_uuid=_UUID_A,
        new_uuid=_UUID_B,
        old_title="GI tab",
        emit_transfer=_emit,
        rename_tab=lambda _a, _t: {"ok": True},
        watch_dir=watch,
    )
    assert len(emitted) == 1
    assert emitted[0]["scope"] == "24600"
    assert emitted[0]["departing_uuid"] == _UUID_A
    assert emitted[0]["successor_uuid"] == _UUID_B
    pin_path = watch / "liaison-budget-exclude-24600.json"
    assert pin_path.is_file()
    data = json.loads(pin_path.read_text(encoding="utf-8"))
    assert data["excluded"][0]["transcript_id"] == _UUID_A


@patch("bus_watch.ide_context_hop_monitor.fetch_registry_claims")
@patch("bus_watch.ide_context_hop_monitor.measure_ide_tab")
@patch("bus_watch.ide_context_hop_monitor.read_lock")
def test_enumerates_all_display8_uuid_rows_monitor(
    mock_lock: MagicMock,
    mock_measure: MagicMock,
    mock_registry: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bus_watch.ide_context_hop_monitor import check_cursor_ide_context_hops

    mock_lock.return_value = {}
    mock_registry.return_value = _registry()
    mock_measure.side_effect = lambda _r, _l, _p, **kw: {
        "transcript_id": kw.get("transcript_id"),
        "used_tokens": 10_000,
        "window_limit_tokens": 256_000,
        "tool_calls": 1,
        "bytes": 100,
        "budget_probe": "glass.cdp",
        "glass_status": "completed",
    }
    emitted: list[dict] = []
    monkeypatch.setattr(
        "bus_watch.events.emit_ide_tab_budget_sampled",
        lambda **kw: emitted.append(dict(kw)),
    )
    rows = [
        _sub_row(sub_id=1, scope="24600", agent="brg-grand-impresario", agent_id=_UUID_A),
        _sub_row(sub_id=2, scope="25291", agent="cursor-houseless", agent_id=_UUID_B),
        _sub_row(sub_id=3, scope="25279", agent="cursor-gi", agent_id=_UUID_C),
        _sub_row(sub_id=4, scope="24600", agent="cursor-extra", agent_id=_UUID_D),
    ]
    store = MagicMock()
    store.subscriptions.return_value = []
    store._bus.return_value = {"subscriptions": rows}

    check_cursor_ide_context_hops(store, mark={}, now=1000.0)
    roots = {row["root_id"] for row in emitted}
    assert "24600" in roots
    sampled = [r["transcript_id"] for r in emitted if r["root_id"] == "24600"]
    assert _UUID_A in sampled
    assert _UUID_D in sampled
