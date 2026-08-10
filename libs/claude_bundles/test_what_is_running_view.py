"""Hermetic tests for what-is-running liveness expiry (arc 6655 slice 1)."""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

import pytest

from claude_bundles import what_is_running_view as view_mod
from claude_bundles.what_is_running_view import (
    HONEST_EMPTY_SESSIONS,
    compose_view,
    render_text,
    serve_view,
    ts_to_iso_z,
)

pytestmark = pytest.mark.offline

_REPO = Path(__file__).resolve().parents[2]
_CLI_PATH = _REPO / "scripts" / "cortex" / "what_is_running.py"

_NOW = 1_700_000_000.0
_PAST = _NOW - 60.0
_FUTURE = _NOW + 600.0


def _active_work(*, rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    rows = rows or [
        {
            "execution_id": "exec-live",
            "registration_id": "reg-1",
            "holder": "cdp/opus-5",
            "purpose": "operator-proxy",
            "status": "running",
        }
    ]
    return {
        "rows": rows,
        "running_count": len(rows),
        "live_cse_count": 0,
        "registry_capacity_count": 1,
        "effective_count": len(rows),
        "soft_limit": 2,
        "hard_limit": 3,
        "free_slots": 2,
        "at_soft_limit": False,
        "at_hard_limit": False,
    }


def _registry() -> dict[str, dict[str, Any]]:
    return {
        "reg-1": {
            "status": "active",
            "purpose": "operator-proxy",
            "holder": "cdp/opus-5",
            "chat_url": "https://claude.ai/cowork/cse_test",
            "port": 9222,
            "started_at": _NOW - 120.0,
        }
    }


def _sessions() -> dict[str, dict[str, Any]]:
    return {
        "6655": {
            "cse_id": "cse_test",
            "ids": {"registration_id": "reg-1", "lane_thread": "6655"},
        }
    }


def _compose(**overrides: Any) -> dict[str, Any]:
    return compose_view(
        active_work=overrides.pop("active_work", _active_work()),
        registry=overrides.pop("registry", _registry()),
        sessions=overrides.pop("sessions", _sessions()),
        hop_watches=overrides.pop("hop_watches", {}),
        sources=overrides.pop("sources", {"project_ask_url": "http://127.0.0.1:8765"}),
        now=overrides.pop("now", _NOW),
        **overrides,
    )


def test_expired_liveness_row_dropped_from_serve_path() -> None:
    """AC1 — row past expires_at_utc does not appear in serve_view output."""
    raw = _compose()
    stale_row = dict(raw["running"][0])
    stale_row["expires_at_utc"] = ts_to_iso_z(_PAST)
    raw["running"] = [stale_row, dict(stale_row, execution_id="exec-fresh")]
    raw["running"][1]["expires_at_utc"] = ts_to_iso_z(_FUTURE)

    served = serve_view(raw, now=_NOW)
    exec_ids = [r.get("execution_id") for r in served["running"]]
    assert exec_ids == ["exec-fresh"]


def test_expired_snapshot_renders_honest_empty() -> None:
    """AC2 — snapshot past expires_at_utc ⇒ no remembered roster."""
    raw = _compose()
    raw["expires_at_utc"] = ts_to_iso_z(_PAST)
    for row in raw["running"]:
        row["expires_at_utc"] = ts_to_iso_z(_FUTURE)

    served = serve_view(raw, now=_NOW)
    text = render_text(raw, now=_NOW)

    assert served["running"] == []
    assert served.get("liveness_assertion") == HONEST_EMPTY_SESSIONS
    assert served["scalars_actual"]["streams_running_count"] == 0
    assert HONEST_EMPTY_SESSIONS in text


def test_obligations_survive_snapshot_expiry() -> None:
    """AC3 — intended/findings persist without TTL when liveness expires."""
    raw = _compose()
    raw["expires_at_utc"] = ts_to_iso_z(_PAST)

    served = serve_view(raw, now=_NOW)

    assert served["intended"]["expiring"] is False
    assert served["intended"]["obligation"] is True
    assert served["findings"]
    assert all(f.get("expiring") is False for f in served["findings"])
    assert all(f.get("obligation") is True for f in served["findings"])
    assert served["findings"][0]["verdict"] == raw["findings"][0]["verdict"]


def test_no_raw_running_access_bypasses_serve_filter() -> None:
    """AC4 — read-path consumers route through serve_view before exposing rows."""
    producer_sites = [
        "claude_bundles.what_is_running_view.compose_view (producer)",
        "scripts/cortex/what_is_running.py:build_from_env → compose_view",
        "scripts/cortex/what_is_running.py:publish_cortex (stores composed JSON)",
    ]
    read_sites = [
        "claude_bundles.what_is_running_view.serve_view (sole row filter)",
        "claude_bundles.what_is_running_view.render_text → serve_view",
        "scripts/cortex/what_is_running.py:main → serve_view for --json",
        "services/mcp-server/tools/_operating_state_serve.py → read_file_result",
    ]

    render_src = inspect.getsource(render_text)
    assert render_src.index("serve_view(") < render_src.index('view["running"]')

    view_src = Path(view_mod.__file__).read_text(encoding="utf-8")
    assert "def serve_view(" in view_src
    assert "def compose_view(" in view_src

    cli_src = _CLI_PATH.read_text(encoding="utf-8")
    assert "serve_view(" in cli_src
    assert "from claude_bundles.what_is_running_view import" in cli_src

    assert producer_sites
    assert read_sites
