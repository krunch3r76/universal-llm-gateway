"""Hop birth claim — acceptance proofs AC1–AC5 and guards G2–G3 (todo:hop-seat-birth-provenance-episode)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from claude_bundles import cdp_registry as reg
from claude_bundles import cdp_registry_store as store
from claude_bundles.cse_provenance import episodes_for_registration, latest_episode
from claude_bundles.cse_url import claude_seat_identity_key
from fastapi.testclient import TestClient
from universal_protocol.errors import ProtocolError

pytestmark = pytest.mark.offline

_LANE = "52001"
_U = "https://claude.ai/cowork/cse_hop_birth_claim_test"
_E = "sat-exec-hop-birth"
_REGISTRY_PATHS = (
    "REGISTRY_DIR",
    "REGISTRY_LOG",
    "ACTIVE_JSON",
    "PORTS_LOCK",
    "REGISTRATIONS_DIR",
)


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    values = {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }
    monkeypatch.setenv("CDP_REGISTRY_SEAT_AUTHORITY", "1")
    files_root = tmp_path / "cortex-files"
    files_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(files_root))
    for name in _REGISTRY_PATHS:
        monkeypatch.setattr(reg._store, name, values[name])
        monkeypatch.setattr(reg, name, values[name])
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    monkeypatch.setattr(reg, "PORT_RANGE", range(9223, 9230))
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    monkeypatch.setattr(
        reg.cdp_lane,
        "profile_for",
        lambda suffix: profiles / f"claude-ai-chrome-profile-{suffix}",
    )
    from services.git_integration_worker import cse_session_holders

    monkeypatch.setattr(
        cse_session_holders,
        "upsert_holder_remote",
        lambda **_kw: cse_session_holders.RemoteUpsert.OK,
    )
    return root


def _noop_launch(port: int, profile: Path) -> int:
    profile.mkdir(parents=True, exist_ok=True)
    return 1


def _mint(holder: str, *, kind: str = "hop", parent: str | None = _LANE) -> Any:
    return reg.register_lane(
        holder=holder,
        purpose="operator-proxy",
        mission_kind=kind,
        parent_thread=parent,
        launch_chrome=_noop_launch,
        is_listening=lambda _p: False,
    )


def _stream(registration_id: str, url: str = _U, execution_id: str = _E) -> None:
    from cdp_ask import runner

    hook = runner._wrap_harvest_with_address(
        None, registration_id=registration_id, execution_id=execution_id
    )
    asyncio.run(hook({"url": url, "streaming": True}))


def _log_indexes(event: str, registration_id: str, url: str = _U) -> list[int]:
    key = claude_seat_identity_key(url)
    return [
        i
        for i, rec in enumerate(store.read_registry_log())
        if rec.get("event") == event
        and rec.get("registration_id") == registration_id
        and claude_seat_identity_key(str(rec.get("chat_url") or "")) == key
    ]


def test_ac1_provenance_read_has_claim_after_first_streaming_sample(
    registry: Path,
) -> None:
    row = _mint("hop")
    r_id = row.registration_id
    _stream(r_id)
    from cdp_ask.app import create_app

    body = (
        TestClient(create_app())
        .get("/v1/cse-session/provenance", params={"registration_id": r_id})
        .json()
    )
    assert body.get("reason") != "no_episode"
    assert body["registration_id"] == r_id
    assert body.get("lane_thread_claim") == _LANE


def test_ac2_claim_episode_precedes_attachment_observed(registry: Path) -> None:
    r_id = _mint("hop").registration_id
    _stream(r_id)
    ep = _log_indexes("cse.provenance.episode", r_id)
    obs = _log_indexes("attachment_observed", r_id)
    assert ep
    assert obs
    assert ep[0] < obs[0]


def test_ac3_end_of_run_bind_restates_claim(registry: Path) -> None:
    from cdp_ask import runner

    r_id = _mint("hop").registration_id
    _stream(r_id)
    first = latest_episode(registration_id=r_id)
    assert first is not None
    runner._persist_session_address(r_id, _U, execution_id=_E)
    eps = episodes_for_registration(r_id)
    assert len(eps) == 1
    assert eps[0].episode_id == first.episode_id


def test_ac4_latch_survives_failing_claim_writer(registry: Path) -> None:
    from cdp_ask import runner

    r_id = _mint("hop").registration_id

    def _raise(*_a: object, **_k: object) -> str:
        raise RuntimeError("claim writer down")

    runner._latch_streaming_attachment(r_id, _U, execution_id=_E, record_claim=_raise)
    assert len(_log_indexes("attachment_observed", r_id)) == 1


def test_ac5_enrich_then_persist_keeps_proven_tail(
    registry: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _associated_reader(_thread: str) -> dict[str, object]:
        return {
            "parent_thread": "bus-parent",
            "lane_role": "side",
            "association_id": 11,
            "state": "associated",
            "lineage_observed_at": 100.0,
        }

    monkeypatch.syspath_prepend(
        str(Path(__file__).resolve().parents[2] / "services" / "mcp-server")
    )
    from tools.agent_bus.cse_provenance_enrich import enrich_request_provenance

    from cdp_ask import runner

    r_id = _mint("hop").registration_id
    _stream(r_id)
    result = enrich_request_provenance(
        lane_thread=_LANE,
        chat_url=_U,
        registration_id=r_id,
        lineage_reader=_associated_reader,
    )
    assert result["ok"] is True
    eps = episodes_for_registration(r_id)
    n = len(eps)
    tail = eps[-1]
    assert tail.lineage_state == "proven"
    assert tail.registration_id == r_id
    runner._persist_session_address(r_id, _U, execution_id=_E)
    after = episodes_for_registration(r_id)
    assert len(after) == n
    assert after[-1].episode_id == tail.episode_id
    assert after[-1].lineage_state == "proven"


def test_g2_non_hop_and_parentless_hop_rows_get_no_claim(registry: Path) -> None:
    root = _mint("root", kind="root")
    parentless = _mint("hop-np", kind="hop", parent=None)
    _stream(root.registration_id, _U + "root")
    _stream(parentless.registration_id, _U + "np")
    assert episodes_for_registration(root.registration_id) == []
    assert episodes_for_registration(parentless.registration_id) == []


def test_g3_conflict_leaves_journal_bytes_unchanged(registry: Path) -> None:
    holder = _mint("holder", kind="root")
    assert reg.bind_session_address(holder.registration_id, chat_url=_U)
    hop = _mint("hop")
    before = store.REGISTRY_LOG.read_bytes()
    with pytest.raises(ProtocolError) as exc:
        _stream(hop.registration_id)
    assert exc.value.code == "attachment.conflict"
    assert store.REGISTRY_LOG.read_bytes() == before
    assert episodes_for_registration(hop.registration_id) == []
