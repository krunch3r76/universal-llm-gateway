"""A2 — registry reads name observed_home_kind (scoped-null ≠ global empty)."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_bundles.cdp_registry_store import (
    classify_observed_home_kind,
    load_active,
    load_active_read,
    load_sessions_read,
)

pytestmark = pytest.mark.offline


def test_classify_dispatch_marker() -> None:
    assert (
        classify_observed_home_kind("/x/cursor-dispatch-homes/auto-1-home") == "dispatch"
    )
    assert classify_observed_home_kind("/home/operator") == "operator"


def test_empty_active_is_scoped_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "cursor-dispatch-homes" / "auto-obs-home"
    registry = home / ".gateway" / "cdp-registry"
    registry.mkdir(parents=True)
    import claude_bundles.cdp_registry_store as store

    monkeypatch.setattr(store, "REGISTRY_DIR", registry)
    monkeypatch.setattr(store, "ACTIVE_JSON", registry / "active.json")
    monkeypatch.setattr(store, "SESSIONS_JSON", registry / "sessions.json")

    assert load_active() == {}
    read = load_active_read()
    assert read.present is False
    assert read.observed_home_kind == "dispatch"
    assert read.miss_label().startswith("observed_home_kind=dispatch")
    sessions = load_sessions_read()
    assert sessions.observed_home_kind == "dispatch"
    assert sessions.present is False
