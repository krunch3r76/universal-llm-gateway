"""POST /boot-mint and perform_boot_mint tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from agent_seat.boot_identity import legacy_boot_identity
from agent_seat.session_id import SESSION_ID_RE

from cortex_store.dispatch_ops.ops_assertions_friction import _op_friction
from cortex_store.dispatch_ops.ops_assertions_write import _op_assert
from cortex_store.routes.boot.mint import perform_boot_mint

_FIXED = datetime(2026, 11, 23, 8, 0, 0, tzinfo=UTC)

_FORBIDDEN_FULL_BRIEF_KEYS = frozenset(
    {
        "briefing_card",
        "sections_available",
        "injected_artifacts",
        "byte_ledger",
        "skills_index_ref",
        "operational_context_ref",
        "audit_dump_path",
        "auto_inject_skills_ref",
    }
)


@pytest.fixture()
def related_db(migrated_db_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    from cortex_store import db as cortex_db

    monkeypatch.setattr(cortex_db, "_CORTEX_DB", migrated_db_path)
    with cortex_db.cortex_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, 'service', ?)",
            ("service:mcp-server", "mcp-server"),
        )
        conn.commit()
    return migrated_db_path


def test_perform_boot_mint_web_anthropic_shape() -> None:
    payload = perform_boot_mint("web-anthropic", at=_FIXED)
    assert "error" not in payload
    assert payload["mint_only"] is True
    assert SESSION_ID_RE.match(payload["session_id"])
    assert payload["session_id"].startswith("web-anthropic-")
    assert _FORBIDDEN_FULL_BRIEF_KEYS.isdisjoint(payload.keys())
    assert payload["boot_identity"]["family"] == "claude"
    assert payload["boot_identity"]["platform"] == "web"


def test_perform_boot_mint_cursor_shape() -> None:
    payload = perform_boot_mint("cursor", at=_FIXED)
    assert payload["session_id"].startswith("cursor-")
    assert SESSION_ID_RE.match(payload["session_id"])


def test_perform_boot_mint_uses_resolve_boot_identity_for_seat() -> None:
    with patch(
        "cortex_store.routes.boot.mint.resolve_boot_identity_for_seat",
        return_value=legacy_boot_identity("claude", "web"),
    ) as resolve:
        perform_boot_mint("web-anthropic", at=_FIXED)
    resolve.assert_called_once_with("web-anthropic")


def test_perform_boot_mint_seat_unresolved() -> None:
    payload = perform_boot_mint("orion", at=_FIXED)
    assert payload.get("reason") == "seat_unresolved"


@pytest.mark.offline
def test_mint_session_id_accepted_by_assert_op(related_db: Path) -> None:
    payload = perform_boot_mint("web-anthropic", at=_FIXED)
    sid = payload["session_id"]
    created = _op_assert(
        entity_id="service:mcp-server",
        claim="Mint-only boot session_id must pass assert related= grammar gate",
        confidence="believed",
        evidence="verified in libs/cortex_store/routes/boot/test_boot_mint.py",
        evidence_uris=["cortex://notes/system/threads/15851-plan-cortex-link-and-grok-bot-seat-fable.md"],
        seeded_by="claude-web",
        derivation_type="agent_observation",
        confidence_score=0.75,
        session_id=sid,
        dry_run=True,
        related=[{"to_node": "service:mcp-server"}],
    )
    assert "error" not in created, created
    assert created.get("reason") != "session_id_grammar"
    assert created["dry_run"] is True
    assert len(created["related_preview"]) == 1


@pytest.mark.offline
def test_mint_session_id_accepted_by_friction_file_dispatch(
    related_db: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    mcp_server = Path(__file__).resolve().parents[4] / "services" / "mcp-server"
    sys.path.insert(0, str(mcp_server))

    payload = perform_boot_mint("cursor", at=_FIXED)
    sid = payload["session_id"]

    def _inproc_file_friction(**kwargs: object) -> dict:
        return _op_friction(
            owner=str(kwargs["owner"]),
            category=str(kwargs.get("category") or "tool_error"),
            note=str(kwargs["note"]),
            agent=str(kwargs["agent"]) if kwargs.get("agent") else None,
            session_id=sid,
        )

    monkeypatch.setattr(
        "tools.agent_bus.friction_file.file_friction",
        _inproc_file_friction,
    )
    monkeypatch.setattr("tools.agent_bus.friction_file.record", lambda *_a, **_k: None)
    from tools.agent_bus.friction_file import _friction_file_dispatch

    result = _friction_file_dispatch(
        owner="service:mcp-server",
        note="substrate_friction_file path with mint-backed session_id at cortex",
        agent="claude-cursor",
    )
    assert "error" not in result, result
    assert result.get("reason") != "session_id_grammar"
    assert int(result["assertion_id"]) > 0
