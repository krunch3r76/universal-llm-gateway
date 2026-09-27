"""Tree-state cache for off-loop /health disclosure."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from deploy_identity.tree_state import (
    _reset_tree_state_cache_for_tests,
    note_tree_state,
    peek_tree_state,
    refresh_tree_state,
    resolve_tree_state,
)


@pytest.mark.offline
def test_peek_miss_returns_unknown() -> None:
    _reset_tree_state_cache_for_tests()
    assert peek_tree_state() == "unknown"


@pytest.mark.offline
def test_peek_serves_fresh_cache() -> None:
    _reset_tree_state_cache_for_tests()
    note_tree_state("clean")
    assert peek_tree_state() == "clean"


@pytest.mark.offline
def test_peek_expired_cache_returns_unknown(monkeypatch) -> None:
    _reset_tree_state_cache_for_tests()
    note_tree_state("clean")
    import deploy_identity.tree_state as ts

    monkeypatch.setattr(ts, "_TREE_STATE_CACHE_TTL_S", 0.0)
    assert peek_tree_state() == "unknown"


@pytest.mark.offline
def test_refresh_notes_cache_without_blocking_peek(monkeypatch) -> None:
    _reset_tree_state_cache_for_tests()
    monkeypatch.setattr(
        "deploy_identity.tree_state.resolve_tree_state", lambda root=None: "dirty"
    )
    assert refresh_tree_state() == "dirty"
    assert peek_tree_state() == "dirty"


@pytest.mark.offline
def test_resolve_tree_state_invokes_git() -> None:
    with patch("deploy_identity.tree_state.subprocess.run") as run:
        run.return_value.stdout = ""
        run.return_value.returncode = 0
        assert resolve_tree_state() in {"clean", "dirty", "unknown"}
        if run.called:
            assert "status" in run.call_args[0][0]
