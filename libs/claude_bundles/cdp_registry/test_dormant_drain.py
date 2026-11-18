"""Unit tests for dormant drain unified chat binding (AC21)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.offline


def test_drain_binds_unified_chat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Dormant row without chat_url binds canonical unified URL via _ensure_chat_url."""
    from claude_bundles import cdp_registry as reg
    from claude_bundles import cse_provenance
    from claude_bundles.cdp_registry.dormant_drain import _ensure_chat_url
    from services.git_integration_worker.cse_session_holders import RemoteUpsert

    uuid = "2762063d-2966-4859-a3ce-23831c6e0c86"
    canonical = f"https://claude.ai/chat/{uuid}"
    www_upper = f"https://www.claude.ai/chat/{uuid.upper()}"
    rid = "reg-dormant-unified"

    root = tmp_path / "cdp-registry"
    root.mkdir()
    regs = root / "registrations"
    regs.mkdir()
    for name, value in {
        "REGISTRY_DIR": root,
        "REGISTRY_LOG": root / "registry.jsonl",
        "ACTIVE_JSON": root / "active.json",
        "PORTS_LOCK": root / "ports.lock",
        "REGISTRATIONS_DIR": regs,
    }.items():
        monkeypatch.setattr(reg._store, name, value)
        monkeypatch.setattr(cse_provenance.store, name, value)
    monkeypatch.setattr(reg._store, "is_seat_authority", lambda: True)
    monkeypatch.setattr(
        "services.git_integration_worker.cse_session_holders.upsert_holder_remote",
        lambda **_kw: RemoteUpsert.OK,
    )
    monkeypatch.setattr(
        "claude_bundles.cdp_orphans._fetch_json",
        lambda _url: [
            {"type": "page", "url": "https://claude.ai/new"},
            {"type": "page", "url": www_upper},
        ],
    )
    row = {
        "registration_id": rid,
        "port": 9231,
        "status": "dormant",
        "purpose": "operator-proxy",
        "chat_url": "",
    }
    reg._store.write_active({rid: row})

    assert _ensure_chat_url(rid, row) is True
    assert reg._store.load_active()[rid]["chat_url"] == canonical
