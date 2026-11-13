"""Houseless registry purpose tag — write path and operator isolation."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from claude_bundles import cdp_registry as reg
from claude_bundles.cdp_registry.driving_seat import ensure_driving_operator_seat
from claude_bundles.cdp_registry.models import RegistryError
from claude_bundles.cdp_registry.registry_purpose import (
    require_registry_tag_purpose,
    set_registration_purpose,
)
from claude_bundles.what_is_running_view import HOUSELESS_PURPOSES, OPERATOR_PURPOSES

pytestmark = pytest.mark.offline


@pytest.fixture
def isolated_registry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    monkeypatch.setattr(reg._store, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg._store, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg._store, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg._store, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg._store, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "REGISTRY_DIR", root)
    monkeypatch.setattr(reg, "REGISTRY_LOG", root / "registry.jsonl")
    monkeypatch.setattr(reg, "ACTIVE_JSON", root / "active.json")
    monkeypatch.setattr(reg, "PORTS_LOCK", root / "ports.lock")
    monkeypatch.setattr(reg, "REGISTRATIONS_DIR", regs)
    monkeypatch.setattr(reg, "_HELD_LOCKS", {})
    return root


def test_houseless_constant_not_in_operator_purposes() -> None:
    assert not HOUSELESS_PURPOSES & OPERATOR_PURPOSES
    assert require_registry_tag_purpose("houseless") == "houseless"


def test_require_registry_tag_purpose_rejects_operator_proxy() -> None:
    with pytest.raises(RegistryError, match="driving operator purpose"):
        require_registry_tag_purpose("operator-proxy")


def test_set_registration_purpose_accepts_houseless(
    isolated_registry: Path,
) -> None:
    host = reg.register_lane(holder="mailbox", purpose="ask", launch=False)
    out = set_registration_purpose(host.registration_id, "houseless")
    assert out.purpose == "houseless"
    row = reg._store.load_active()[host.registration_id]
    assert row["purpose"] == "houseless"


def test_set_registration_purpose_rejects_non_listable_status(
    isolated_registry: Path,
) -> None:
    host = reg.register_lane(holder="mailbox", purpose="ask", launch=False)
    reg_id = host.registration_id
    reg._store.write_active(
        {
            **reg._store.load_active(),
            reg_id: {
                **reg._store.load_active()[reg_id],
                "status": "allocating",
            },
        }
    )
    with pytest.raises(RegistryError, match="not host-listable"):
        set_registration_purpose(reg_id, "houseless")


def test_set_registration_purpose_rejects_prior_operator_purpose(
    isolated_registry: Path,
) -> None:
    host = reg.register_lane(holder="mailbox", purpose="ask", launch=False)
    reg_id = host.registration_id
    reg._store.write_active(
        {
            **reg._store.load_active(),
            reg_id: {
                **reg._store.load_active()[reg_id],
                "purpose": "operator-proxy",
            },
        }
    )
    with pytest.raises(RegistryError, match="not admissible"):
        set_registration_purpose(reg_id, "houseless")


def test_set_registration_purpose_rejects_seated_row(
    isolated_registry: Path,
) -> None:
    minted = reg.register_lane(holder="seat", purpose="ask", launch=False)
    reg._store.write_active(
        {
            **reg._store.load_active(),
            minted.registration_id: {
                **reg._store.load_active()[minted.registration_id],
                "seat_lane": "22760",
                "seat_bound_at": 1.0,
            },
        }
    )
    with pytest.raises(RegistryError, match="open driving seat"):
        set_registration_purpose(minted.registration_id, "houseless")


def test_driving_seat_refuses_houseless(
    isolated_registry: Path,
) -> None:
    with pytest.raises(RegistryError, match="driving operator purpose must be one of"):
        ensure_driving_operator_seat(
            holder="bad",
            parent_thread="22760",
            purpose="houseless",
            launch_chrome=lambda *_a, **_k: 1,
            is_listening=lambda _p: False,
        )


def test_lane_seat_holder_ignores_houseless(monkeypatch: pytest.MonkeyPatch) -> None:
    from cdp_ask.lane_admission import lane_seat_holder
    from cdp_ask.operator_seat_resolve import resolve_operator_seat

    monkeypatch.setattr(
        "cdp_ask.operator_seat_resolve._provenance_get",
        lambda *_a, **_k: None,
    )

    snap = {
        "observed_at": "2026-11-09T00:00:00Z",
        "seat_rows": [
            {
                "purpose": "houseless",
                "seat_lane": "22760",
                "registration_id": "583b411df34247f9a48bc044b6518be3",
            },
            {
                "purpose": "operator-proxy",
                "parent_thread": "22760",
                "registration_id": "op-seat",
                "seat_bound_at": 10.0,
            },
        ],
    }
    holder = lane_seat_holder(snap, "22760")
    assert holder["registration_id"] == "op-seat"
    resolved = resolve_operator_seat("22760", get_lane_snapshot=lambda: snap)
    assert resolved["registration_id"] == "op-seat"


def test_houseless_bind_claims_bus_mailbox_lane_thread(
    isolated_registry: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """STATUS_TAB row + seated hook + set_tab_chat_url (runner order) → lane claim."""
    from agent_bus_store.db import create_thread, init_db
    from claude_bundles import cse_provenance
    import time
    import uuid

    from claude_bundles.cdp_registry.models import STATUS_TAB
    from claude_bundles.cdp_registry.tab_rows import set_tab_chat_url
    from systems.frontier_consult.cdp_hop_cse_bind import associate_hop_seated_cse

    db_path = tmp_path / "bus.db"
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(db_path))
    init_db()
    detail = create_thread(thread_id=None, slug="hl-prov-mailbox", tags=["mailbox"])
    assert detail is not None
    mailbox = str(detail["id"])
    host = reg.register_lane(holder="cdp-tab-pool", purpose="tab_host", launch=False)
    tab_id = uuid.uuid4().hex
    with reg._store.ports_lock():
        active = reg._store.load_active()
        active[tab_id] = {
            "registration_id": tab_id,
            "status": STATUS_TAB,
            "host_registration_id": host.registration_id,
            "port": host.port,
            "holder": "seat",
            "purpose": "houseless",
            "mission_kind": None,
            "parent_thread": None,
            "started_at": time.time(),
        }
        reg._store.write_active(active)
    url = "https://claude.ai/cowork/cse_hl_prov"
    exec_id = "a1a2a3a4-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    with patch("agent_bus_store.db.cse_associations.emit_cse_bound"):
        associate_hop_seated_cse(
            mission_kind=None,
            thread_id=mailbox,
            parent_thread=None,
            chat_url=url,
            registration_id=tab_id,
            execution_id=exec_id,
            purpose="houseless",
            mailbox_thread=mailbox,
        )
    set_tab_chat_url(tab_id, chat_url=url, execution_id=exec_id)
    active = reg._store.load_active()[tab_id]
    assert active.get("status") == STATUS_TAB
    assert not str(active.get("parent_thread") or "").strip()
    episode = cse_provenance.latest_episode(chat_url=url)
    assert episode is not None
    assert episode.lane_thread == mailbox
    resolved = cse_provenance.resolve(chat_url=url, registration_id=tab_id)
    assert resolved.get("lane_thread_claim") == mailbox
    assert resolved.get("reason") != "no_episode"


def test_register_lane_houseless_rejects_parent_thread(
    isolated_registry: Path,
) -> None:
    with pytest.raises(
        RegistryError, match="houseless purpose cannot bind parent_thread"
    ):
        reg.register_lane(
            holder="mailbox",
            purpose="houseless",
            parent_thread="22760",
            launch=False,
        )
