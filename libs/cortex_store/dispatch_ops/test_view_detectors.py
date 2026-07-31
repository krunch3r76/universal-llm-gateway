"""Tests for derived-view audit detectors."""

from __future__ import annotations

import json

import pytest

from cortex_store.dispatch_ops import _shared as dispatch_shared
from cortex_store.dispatch_ops._detectors.views import (
    detect_playbook_stale,
    detect_view_core_hash_mismatch,
)
from cortex_store.dispatch_ops.ops_audit import _op_audit
from cortex_store.dispatch_ops.ops_entities import _op_entity_create
from cortex_store.dispatch_ops.ops_views import _op_view_render
from cortex_store.db import cortex_conn


@pytest.fixture()
def view_env(migrated_db_path, tmp_path, monkeypatch):
    from cortex_store.conftest import bind_cortex_db

    files_root = tmp_path / "files"
    files_root.mkdir()
    bind_cortex_db(monkeypatch, migrated_db_path)
    monkeypatch.setattr(dispatch_shared, "_FILES_ROOT", files_root)
    monkeypatch.setattr("cortex_store.dispatch_ops.ops_views._FILES_ROOT", files_root)
    return {"files_root": files_root}


def test_fresh_view_no_findings(view_env) -> None:
    _op_entity_create(id="case:det", type="case", name="Det Case")
    _op_entity_create(
        id="document:det-charter",
        type="document",
        name="Det Charter",
        source_uri="cortex://notes/views/det-charter.md",
    )
    _op_view_render(
        document_id="document:det-charter",
        mode="register",
        root_id="case:det",
        view_profile="matter_charter",
        narrative_sections={"narrative_layer": "Fresh [assertion:1]."},
    )
    with cortex_conn() as conn:
        stale = detect_playbook_stale(conn, "document:det-charter")
        mismatch = detect_view_core_hash_mismatch(conn, "document:det-charter")
    assert stale == []
    assert mismatch == []


def test_stale_fixture_after_assertion(view_env) -> None:
    _op_entity_create(id="case:stale", type="case", name="Stale Case")
    _op_entity_create(
        id="document:stale-charter",
        type="document",
        name="Stale Charter",
        source_uri="cortex://notes/views/stale-charter.md",
    )
    _op_view_render(
        document_id="document:stale-charter",
        mode="register",
        root_id="case:stale",
        view_profile="matter_charter",
        narrative_sections={"narrative_layer": "Stale seed [assertion:10]."},
    )
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO assertions (entity_id, claim, confidence) VALUES (?, ?, ?)",
            ("case:stale", "New assertion after snapshot", "believed"),
        )
        conn.commit()
        findings = detect_playbook_stale(conn, "document:stale-charter")
    assert findings
    payload = json.loads(findings[0]["detail"])
    assert "view_span" in payload
    assert payload["verdict"] == "watched_set_high_water"


def test_core_hash_mismatch_on_hand_edit(view_env) -> None:
    _op_entity_create(id="case:edit", type="case", name="Edit Case")
    _op_entity_create(
        id="document:edit-charter",
        type="document",
        name="Edit Charter",
        source_uri="cortex://notes/views/edit-charter.md",
    )
    _op_view_render(
        document_id="document:edit-charter",
        mode="register",
        root_id="case:edit",
        view_profile="matter_charter",
        narrative_sections={"narrative_layer": "Edit seed [assertion:20]."},
    )
    path = view_env["files_root"] / "notes/views/edit-charter.md"
    body = path.read_text(encoding="utf-8")
    body = body.replace(
        "<!-- view-core:header_block:begin -->",
        "<!-- view-core:header_block:begin -->\nHAND-EDITED",
    )
    path.write_text(body, encoding="utf-8")
    with cortex_conn() as conn:
        findings = detect_view_core_hash_mismatch(conn, "document:edit-charter")
    assert findings
    assert findings[0]["kind"] == "view_core_hash_mismatch"


def test_audit_surfaces_playbook_stale(view_env) -> None:
    _op_entity_create(id="case:audit", type="case", name="Audit Case")
    _op_entity_create(
        id="document:audit-charter",
        type="document",
        name="Audit Charter",
        source_uri="cortex://notes/views/audit-charter.md",
    )
    _op_view_render(
        document_id="document:audit-charter",
        mode="register",
        root_id="case:audit",
        view_profile="matter_charter",
        narrative_sections={"narrative_layer": "Audit [assertion:30]."},
    )
    with cortex_conn() as conn:
        conn.execute(
            "INSERT INTO assertions (entity_id, claim, confidence) VALUES (?, ?, ?)",
            ("case:audit", "Trigger stale", "believed"),
        )
        conn.commit()
    result = _op_audit(kinds=["playbook_stale"], subject="document:audit-charter")
    assert "error" not in result
    kinds = {f["kind"] for f in result.get("findings", [])}
    assert "playbook_stale" in kinds
