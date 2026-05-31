"""Unit tests for grokbuild.auth_notifier — latch, debounce, payload shape."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from grokbuild.auth_notifier import (
    _latch_active,
    _latch_path,
    clear_notification_latch,
    notify_if_needed,
)

# ── helpers ──────────────────────────────────────────────────────────────────

_DEFAULTS = dict(
    agent_bus_url="http://agent-bus.local",
    agent_bus_token="tok",
    notify_slug="grokbuild-auth-alert",
    notify_to="claude-cursor",
    debounce_h=12,
    trigger="test-trigger",
    grok_auth_dir="/home/user/.grok",
    deploy_shape="local",
)


def _fake_post_turn(status: int = 201):
    """Return a _post_turn replacement that returns (status, 'ok') and records calls."""
    calls: list[dict] = []

    def _post(**kwargs):
        calls.append(kwargs)
        return status, "ok"

    return _post, calls


def _install_post_turn(monkeypatch: pytest.MonkeyPatch, status: int = 201):
    fake, calls = _fake_post_turn(status)
    monkeypatch.setattr("grokbuild.auth_notifier._post_turn", fake)
    return calls


# ── token / latch guard ──────────────────────────────────────────────────────


def test_empty_token_returns_false_no_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No agent_bus_token → False, no POST, no latch written."""
    calls = _install_post_turn(monkeypatch)
    result = notify_if_needed(
        sidecar_dir=tmp_path, **{**_DEFAULTS, "agent_bus_token": ""}
    )
    assert result is False
    assert not calls
    assert not _latch_path(tmp_path).exists()


def test_active_latch_returns_false_no_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Latch present and within debounce window → False, no POST."""
    calls = _install_post_turn(monkeypatch)
    _latch_path(tmp_path).write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    result = notify_if_needed(sidecar_dir=tmp_path, **_DEFAULTS)
    assert result is False
    assert not calls


# ── successful notification ──────────────────────────────────────────────────


def test_post_201_returns_true_writes_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST 201 → True; latch file written with parseable ISO timestamp."""
    _install_post_turn(monkeypatch, status=201)
    result = notify_if_needed(sidecar_dir=tmp_path, **_DEFAULTS)

    assert result is True
    latch = _latch_path(tmp_path)
    assert latch.exists()
    # latch must contain a parseable UTC ISO timestamp
    ts = datetime.fromisoformat(latch.read_text().strip())
    assert ts.tzinfo is not None


def test_post_201_appends_debounce_key_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """debounce_key_out receives the latch ISO timestamp when POST succeeds."""
    _install_post_turn(monkeypatch, status=201)
    key_out: list[str] = []
    notify_if_needed(sidecar_dir=tmp_path, **_DEFAULTS, debounce_key_out=key_out)
    assert len(key_out) == 1
    # must be parseable as datetime
    ts = datetime.fromisoformat(key_out[0])
    assert ts.tzinfo is not None


# ── transport error ──────────────────────────────────────────────────────────


def test_post_error_returns_false_no_latch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POST 599 (transport error) → False; latch NOT written (so next trigger retries)."""
    _install_post_turn(monkeypatch, status=599)
    result = notify_if_needed(sidecar_dir=tmp_path, **_DEFAULTS)
    assert result is False
    assert not _latch_path(tmp_path).exists()


# ── payload shape (§F2 guard) ────────────────────────────────────────────────


def test_payload_has_slug_not_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_post_turn receives slug==notify_slug, from=='grokbuild-worker', no 'thread' key."""
    import transport_utils

    captured_json: list[dict] = []

    class _FakeResp:
        status_code = 201
        text = "created"

    class _FakeClient:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def post(self, path, *, headers, json):
            captured_json.append(json)
            return _FakeResp()

    monkeypatch.setattr(
        transport_utils, "make_sync_client", lambda *a, **kw: _FakeClient()
    )

    slug = "grokbuild-auth-alert"
    notify_if_needed(sidecar_dir=tmp_path, **{**_DEFAULTS, "notify_slug": slug})

    assert len(captured_json) == 1
    payload = captured_json[0]
    assert payload.get("slug") == slug
    assert payload.get("from") == "grokbuild-worker"
    assert "thread" not in payload


# ── clear_notification_latch ─────────────────────────────────────────────────


def test_clear_notification_latch_removes_file(tmp_path: Path) -> None:
    """clear_notification_latch removes the latch file when present."""
    latch = _latch_path(tmp_path)
    latch.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")

    clear_notification_latch(tmp_path)

    assert not latch.exists()


def test_clear_notification_latch_idempotent(tmp_path: Path) -> None:
    """clear_notification_latch is idempotent when latch is absent."""
    clear_notification_latch(tmp_path)  # must not raise


# ── _latch_active edge cases ──────────────────────────────────────────────────


def test_latch_active_returns_false_for_expired_latch(tmp_path: Path) -> None:
    """A latch timestamp older than debounce_h hours → fail-open (False → will notify)."""
    old_ts = datetime.now(UTC) - timedelta(hours=25)
    _latch_path(tmp_path).write_text(old_ts.isoformat(), encoding="utf-8")
    assert _latch_active(tmp_path, debounce_h=12) is False


def test_latch_active_returns_false_for_garbage_content(tmp_path: Path) -> None:
    """Non-parseable latch content → fail-open (False → will notify)."""
    _latch_path(tmp_path).write_text("not-a-timestamp", encoding="utf-8")
    assert _latch_active(tmp_path, debounce_h=12) is False
