"""Registry binding retire — provenance close, resolve, fleet read, idempotency."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles import cse_provenance
from claude_bundles.cdp_registry.binding_retire import (
    resolve_retire_target,
    retire_dormant_binding,
)
from claude_bundles.cse_provenance_retire import append_binding_retired_episode

pytestmark = pytest.mark.offline

_CSE = "https://claude.ai/cowork/cse_retire_spec"
_RETIRE_LOG_EVENT = "dormant_binding_retired"


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    for name, value in {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "SESSIONS_JSON": root / "sessions.json",
        "SESSION_TRANSITIONS_JSONL": root / "session_transitions.jsonl",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }.items():
        monkeypatch.setattr(reg._store, name, value)
        monkeypatch.setattr(cse_provenance.store, name, value)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_DIR", root)
    monkeypatch.setattr(cse_provenance.store, "REGISTRY_LOG", root / "registry.jsonl")
    reg._store.ensure_dirs()
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 4242


def _dormant_seat(url: str = _CSE) -> str:
    seat = reg.register_lane(
        holder="dormancy-test",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(seat.registration_id, chat_url=url)
    reg._release_driver_lock(seat.registration_id)
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    return seat.registration_id


def test_retire_misbound_appends_provenance_journal(isolated_registry: Path) -> None:
    from claude_bundles.cdp_registry.session_address import (
        retire_misbound_dormant_attachment,
    )

    rid = _dormant_seat()
    with reg._store.ports_lock():
        active = reg._store.load_active()
        row = dict(active[rid])
        row["seat_closed_at"] = 1.0
        row["seat_close_reason"] = "dormant_holder_misbound"
        active[rid] = row
        reg._store.write_active(active)
    assert retire_misbound_dormant_attachment(rid) is True
    log = (isolated_registry / "registry.jsonl").read_text()
    assert "cse.provenance.episode" in log
    assert '"state": "retired"' in log


def test_provenance_resolve_skips_retired_episode(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = _dormant_seat()
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=rid,
        cdp_url="http://127.0.0.1:9240",
        lane_thread="lane-a",
    )
    append_binding_retired_episode(
        chat_url=_CSE,
        registration_id=rid,
        reason="test_close",
    )
    monkeypatch.setattr(
        "claude_bundles.cse_provenance_resolve.is_row_present",
        lambda _rid: True,
    )
    resolved = cse_provenance.resolve(chat_url=_CSE)
    assert resolved["state"] == "unbound"
    assert resolved.get("host_state") == "not_listable"


def test_retire_dormant_binding_on_local_row(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    out = retire_dormant_binding(registration_id=rid, reason="unit_test")
    assert out["outcome"] == "retired"
    assert out["idempotent"] is False
    row = reg._store.load_active()[rid]
    assert not str(row.get("chat_url") or "").strip()
    log = (isolated_registry / "registry.jsonl").read_text()
    assert _RETIRE_LOG_EVENT in log
    assert '"state": "retired"' in log


def test_retire_resolves_row_via_fleet_registry_only(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid = "fleet-only-reg"
    fleet_doc = {
        "availability": "ok",
        "seats": [
            {
                "registration_id": rid,
                "status": "dormant",
                "chat_url": _CSE,
            }
        ],
    }
    with patch(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        return_value=fleet_doc,
    ):
        found_rid, url, row = resolve_retire_target(chat_url=_CSE)
    assert found_rid == rid
    assert url == _CSE
    assert row is not None
    assert row["status"] == "dormant"


def test_retire_journal_only_when_active_row_already_unbound(
    isolated_registry: Path,
) -> None:
    rid = _dormant_seat()
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=rid,
        cdp_url="http://127.0.0.1:9241",
    )
    with reg._store.ports_lock():
        active = reg._store.load_active()
        row = dict(active[rid])
        row.pop("chat_url", None)
        active[rid] = row
        reg._store.write_active(active)
    out = retire_dormant_binding(chat_url=_CSE, reason="journal_backfill")
    assert out["outcome"] == "retired"
    assert cse_provenance.resolve(chat_url=_CSE)["state"] == "unbound"


def test_retire_absent_target_is_idempotent_noop(isolated_registry: Path) -> None:
    out = retire_dormant_binding(
        registration_id="missing-reg",
        chat_url="https://claude.ai/cowork/cse_never_seen",
    )
    assert out["outcome"] == "noop"
    assert out["idempotent"] is True


def test_repeat_retire_is_idempotent(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    first = retire_dormant_binding(registration_id=rid)
    second = retire_dormant_binding(registration_id=rid, chat_url=_CSE)
    assert first["outcome"] == "retired"
    assert second["outcome"] == "noop"
    assert second["idempotent"] is True


def test_retire_audit_log_written(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    retire_dormant_binding(registration_id=rid, reason="audit_probe")
    records = [
        json.loads(line)
        for line in (isolated_registry / "registry.jsonl").read_text().splitlines()
        if line.strip()
    ]
    audits = [r for r in records if r.get("event") == _RETIRE_LOG_EVENT]
    assert len(audits) == 1
    assert audits[0]["actor"]
    assert audits[0]["before"]["chat_url"] == _CSE


def test_fleet_resolve_does_not_invoke_ssh(isolated_registry: Path) -> None:
    with patch(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        return_value={"availability": "unavailable", "seats": None},
    ) as fleet_read:
        resolve_retire_target(registration_id="x")
    fleet_read.assert_called_once()
    assert "ssh" not in fleet_read.call_args.kwargs
