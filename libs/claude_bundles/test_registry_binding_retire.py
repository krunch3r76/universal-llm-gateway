"""Registry binding retire — provenance close, guards, authority, idempotency."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from universal_protocol.errors import ProtocolError

from claude_bundles import cdp_registry as reg
from claude_bundles import cdp_registry_events, cse_provenance
from claude_bundles.cdp_registry.binding_retire import (
    resolve_retire_target,
    retire_dormant_binding,
)
from claude_bundles.cdp_registry.binding_retire_models import RETIRE_LOG_EVENT
from claude_bundles.cse_provenance_retire import append_binding_retired_episode

pytestmark = pytest.mark.offline

_CSE = "https://claude.ai/cowork/cse_retire_spec"
_CSE_B = "https://claude.ai/cowork/cse_retire_other"


@pytest.fixture(autouse=True)
def _fleet_default_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        lambda **_kw: {"availability": "unavailable", "seats": None},
    )


@pytest.fixture(autouse=True)
def _registry_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: True)


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
    reg._store.ensure_dirs()
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 4242


def _dormant_seat(url: str = _CSE, purpose: str = "operator-proxy") -> str:
    seat = reg.register_lane(
        holder="dormancy-test",
        purpose=purpose,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(seat.registration_id, chat_url=url)
    reg._release_driver_lock(seat.registration_id)
    reg.make_dormant(seat.registration_id, is_listening=lambda _p: False)
    return seat.registration_id


def _live_seat(url: str = _CSE) -> str:
    seat = reg.register_lane(
        holder="live-test",
        purpose="operator-proxy",
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )
    reg.bind_session_address(seat.registration_id, chat_url=url)
    reg._release_driver_lock(seat.registration_id)
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


def test_retire_dormant_binding_on_local_row(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    out = retire_dormant_binding(
        registration_id=rid, reason="unit_test", caller_agent="test-seat"
    )
    assert out["outcome"] == "retired"
    assert not str(reg._store.load_active()[rid].get("chat_url") or "").strip()
    assert RETIRE_LOG_EVENT in (isolated_registry / "registry.jsonl").read_text()


def test_dry_run_writes_nothing(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    before = (isolated_registry / "registry.jsonl").read_text()
    out = retire_dormant_binding(
        registration_id=rid, dry_run=True, reason="preview"
    )
    assert out["outcome"] == "dry_run"
    assert (isolated_registry / "registry.jsonl").read_text() == before


def test_refuse_live_local_row(isolated_registry: Path) -> None:
    rid = _live_seat()
    with pytest.raises(ProtocolError) as exc:
        retire_dormant_binding(registration_id=rid, reason="nope")
    assert exc.value.data.get("reason") == "not_dormant"


def test_refuse_fleet_only_live_row(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet_doc = {
        "availability": "ok",
        "seats": [
            {
                "registration_id": "fleet-live",
                "status": "active",
                "chat_url": _CSE,
            }
        ],
    }
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        lambda **_kw: fleet_doc,
    )
    with pytest.raises(ProtocolError) as exc:
        retire_dormant_binding(registration_id="fleet-live", chat_url=_CSE, reason="n")
    assert exc.value.data.get("reason") == "not_dormant"


def test_refuse_empty_status_row(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet_doc = {
        "availability": "ok",
        "seats": [{"registration_id": "fleet-blank", "status": "", "chat_url": _CSE}],
    }
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        lambda **_kw: fleet_doc,
    )
    with pytest.raises(ProtocolError) as exc:
        retire_dormant_binding(registration_id="fleet-blank", chat_url=_CSE, reason="n")
    assert exc.value.data.get("reason") == "not_dormant"


def test_refuse_registration_mismatch(isolated_registry: Path) -> None:
    owner = _dormant_seat(_CSE)
    other = _dormant_seat(_CSE_B)
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=owner,
        cdp_url="http://127.0.0.1:9242",
    )
    with pytest.raises(ProtocolError) as exc:
        retire_dormant_binding(registration_id=other, chat_url=_CSE, reason="n")
    assert exc.value.data.get("reason") == "registration_mismatch"


def test_rebind_after_retire_becomes_current(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid_a = _dormant_seat(_CSE)
    rid_b = _dormant_seat(_CSE_B)
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=rid_a,
        cdp_url="http://127.0.0.1:9243",
    )
    retire_dormant_binding(registration_id=rid_a, chat_url=_CSE, reason="close_a")
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=rid_b,
        cdp_url="http://127.0.0.1:9244",
    )
    monkeypatch.setattr(
        "claude_bundles.cse_provenance_resolve.is_row_present",
        lambda rid: rid == rid_b,
    )
    resolved = cse_provenance.resolve(chat_url=_CSE)
    assert resolved["registration_id"] == rid_b
    assert resolved["state"] == "current"


def test_late_retire_a_does_not_shadow_b(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rid_a = _dormant_seat(_CSE)
    rid_b = _dormant_seat(_CSE_B)
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=rid_a,
        cdp_url="http://127.0.0.1:9245",
    )
    cse_provenance.append_episode(
        chat_url=_CSE,
        registration_id=rid_b,
        cdp_url="http://127.0.0.1:9246",
    )
    with pytest.raises(ProtocolError):
        retire_dormant_binding(registration_id=rid_a, chat_url=_CSE, reason="late")
    monkeypatch.setattr(
        "claude_bundles.cse_provenance_resolve.is_row_present",
        lambda rid: rid == rid_b,
    )
    assert cse_provenance.resolve(chat_url=_CSE)["registration_id"] == rid_b


def test_not_registry_authority_fleet_only(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fleet_doc = {
        "availability": "ok",
        "seats": [
            {
                "registration_id": "fleet-dormant",
                "status": "dormant",
                "chat_url": _CSE,
            }
        ],
    }
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        lambda **_kw: fleet_doc,
    )
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: False)
    with pytest.raises(ProtocolError) as exc:
        retire_dormant_binding(registration_id="fleet-dormant", chat_url=_CSE, reason="n")
    assert exc.value.data.get("reason") == "not_registry_authority"


def test_subprocess_ssh_not_used_on_retire(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    with patch("subprocess.run", side_effect=AssertionError("ssh subprocess")):
        with patch("subprocess.Popen", side_effect=AssertionError("ssh popen")):
            retire_dormant_binding(registration_id=rid, reason="no_ssh")
    assert True


def test_repeat_retire_is_idempotent(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    first = retire_dormant_binding(registration_id=rid, reason="once")
    second = retire_dormant_binding(registration_id=rid, chat_url=_CSE, reason="twice")
    assert first["outcome"] == "retired"
    assert second["outcome"] == "noop"


def test_retire_absent_target_is_idempotent_noop(isolated_registry: Path) -> None:
    out = retire_dormant_binding(
        registration_id="missing-reg",
        chat_url="https://claude.ai/cowork/cse_never_seen",
        reason="absent",
    )
    assert out["outcome"] == "noop"


def test_retire_journal_only_when_active_unbound(isolated_registry: Path) -> None:
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


def test_retire_audit_log_correlates_episode(isolated_registry: Path) -> None:
    rid = _dormant_seat()
    retire_dormant_binding(
        registration_id=rid, reason="audit_probe", caller_agent="audit-seat"
    )
    records = [
        json.loads(line)
        for line in (isolated_registry / "registry.jsonl").read_text().splitlines()
        if line.strip()
    ]
    audits = [r for r in records if r.get("event") == RETIRE_LOG_EVENT]
    episodes = [r for r in records if r.get("event") == "cse.provenance.episode"]
    assert audits[0]["caller_agent"] == "audit-seat"
    assert audits[0]["episode_id"] == episodes[-1]["episode_id"]


def test_retired_episode_skips_bound_events(
    isolated_registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    emitted: list[str] = []
    monkeypatch.setattr(
        cdp_registry_events,
        "emit",
        lambda event: emitted.append(event.signal),
    )
    append_binding_retired_episode(
        chat_url=_CSE,
        registration_id="reg-event",
        reason="evt",
    )
    assert emitted == []


def test_fleet_resolve_patch(isolated_registry: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fleet_doc = {
        "availability": "ok",
        "seats": [
            {"registration_id": "fleet-only", "status": "dormant", "chat_url": _CSE}
        ],
    }
    monkeypatch.setattr(
        "claude_bundles.cdp_registry_remote_read.read_fleet_registry",
        lambda **_kw: fleet_doc,
    )
    found_rid, url, row = resolve_retire_target(chat_url=_CSE)
    assert found_rid == "fleet-only"
    assert row["status"] == "dormant"
