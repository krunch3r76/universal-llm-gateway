"""POST /boot-mint and perform_boot_mint tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from agent_seat.boot_identity import legacy_boot_identity
from agent_seat.session_id import SESSION_ID_RE

from cortex_store.dispatch_ops._related_edges import prepare_related_edges
from cortex_store.dispatch_ops.ops_edges import _op_edge_create
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


def test_mint_session_id_passes_edge_create_grammar() -> None:
    payload = perform_boot_mint("web-anthropic", at=_FIXED)
    sid = payload["session_id"]
    with patch(
        "cortex_store.dispatch_ops.ops_edges._create_edge_impl",
        return_value={"edge_id": 1},
    ):
        edge = _op_edge_create(
            session_id=sid,
            agent="claude-web",
            from_node="assertion:1",
            to_node="service:mcp-server",
            edge_type="relates_to",
        )
    assert edge.get("reason") != "session_id_grammar"


def test_mint_session_id_passes_friction_related_grammar() -> None:
    payload = perform_boot_mint("web-anthropic", at=_FIXED)
    sid = payload["session_id"]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [{"type": "relates_to"}]
    _, err = prepare_related_edges(
        conn,
        [{"to_node": "service:mcp-server"}],
        session_id=sid,
        agent="claude-web",
    )
    assert err is None or err.get("reason") != "session_id_grammar"
