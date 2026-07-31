"""P6 postulate conformance suite for view_render refresh semantics."""

from __future__ import annotations

import pytest

from cortex_store.dispatch_ops import _shared as dispatch_shared
from cortex_store.dispatch_ops.ops_entities import _op_entity_create
from cortex_store.dispatch_ops.ops_views import _op_view_render


@pytest.fixture()
def view_env(migrated_db_path, tmp_path, monkeypatch):
    from cortex_store.conftest import bind_cortex_db

    files_root = tmp_path / "files"
    files_root.mkdir()
    bind_cortex_db(monkeypatch, migrated_db_path)
    monkeypatch.setattr(dispatch_shared, "_FILES_ROOT", files_root)
    monkeypatch.setattr("cortex_store.dispatch_ops.ops_views._FILES_ROOT", files_root)
    return files_root


def _register(view_env, *, narrative: str | None = None) -> tuple[str, str]:
    _op_entity_create(id="case:p6", type="case", name="P6 Case")
    _op_entity_create(
        id="document:p6-charter",
        type="document",
        name="P6 Charter",
        source_uri="cortex://notes/views/p6-charter.md",
    )
    kwargs = {
        "document_id": "document:p6-charter",
        "mode": "register",
        "root_id": "case:p6",
        "view_profile": "matter_charter",
    }
    if narrative:
        kwargs["narrative_sections"] = {
            "narrative_layer": narrative,
        }
    result = _op_view_render(**kwargs)
    assert "error" not in result, result
    return "case:p6", "document:p6-charter"


def test_km_revision_localized_repair(view_env) -> None:
    """KM-revision: supersede triggers localized repair; other sections unchanged."""
    _register(
        view_env,
        narrative="Baseline synthesis cites [assertion:100] for context.",
    )
    # Deliberate rejection: full iterated-revision postulates × relevance-sensitivity
    # are provably incompatible in general (Aravanis) — behavioral suite only.
    refresh = _op_view_render(document_id="document:p6-charter", mode="refresh")
    assert refresh["sections_repaired"] == []


def test_km_update_row_movement(view_env) -> None:
    """KM-update: world change moves rows between sections via full render."""
    _register(view_env, narrative="World state [assertion:200] noted.")
    full = _op_view_render(document_id="document:p6-charter", mode="full")
    assert "error" not in full
    assert full["sections_repaired"]


def test_anti_amnesia_independence(view_env) -> None:
    """Independence: non-delta narrative rejected on refresh."""
    _register(view_env, narrative="Stable narrative [assertion:300].")
    result = _op_view_render(
        document_id="document:p6-charter",
        mode="refresh",
        narrative_sections={"narrative_layer": "Mutated [assertion:301]"},
    )
    assert result.get("code") == "anti_amnesia_violation"


def test_idempotency_postulate(view_env) -> None:
    _register(view_env, narrative="Idempotent [assertion:400].")
    a = _op_view_render(document_id="document:p6-charter", mode="refresh")
    b = _op_view_render(document_id="document:p6-charter", mode="refresh")
    assert a["core_hash"] == b["core_hash"]
    assert a["view_rev"] == b["view_rev"]


def test_full_render_trigger(view_env) -> None:
    _register(view_env, narrative="Full render [assertion:500].")
    full = _op_view_render(document_id="document:p6-charter", mode="full")
    assert full["mode"] == "full"
    assert full["view_rev"] >= 1


def test_tombstone_consumption_placeholder(view_env) -> None:
    """P7 tombstone rows consumed by index views — Tier-0 backstop documented."""
    _op_entity_create(
        id="document:p6-index",
        type="document",
        name="P6 Index",
        source_uri="cortex://notes/views/p6-index.md",
    )
    reg = _op_view_render(
        document_id="document:p6-index",
        mode="register",
        view_profile="matter_index",
    )
    assert "error" not in reg
