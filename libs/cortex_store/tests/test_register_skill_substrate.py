"""Regression tests for register_skill_substrate (friction 22236).

Pins the two compounding defects that produced a deterministic HTTP 500
``OperationalError: cannot rollback - no transaction is active`` when
registering over an EXISTING agent_skill entity:

1. Primary (masked): ``entities.attributes`` is a JSON TEXT column and
   ``query()`` does not decode it — ``attrs.get(...)`` raised
   AttributeError on any existing row with non-null attributes.
2. Secondary (surfaced): the existing-entity branch ends the explicit
   transaction, then the unconditional ROLLBACK in the ``except`` handler
   double-rolled-back and masked the original exception.

Also covers: divergent-existing → composite_conflict whose ``suggested``
update covers name/source_uri (so applying it converges), matching-existing
→ document + keystone relationship backfill (the substantiation-migration
shape), and the fresh-create orchestration staying green.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from cortex_store import db as cortex_db
from cortex_store.dispatch_ops import ops_composites

SKILL_ID = "testskill"
SKILL_ENTITY = f"agent_skill:{SKILL_ID}"
DOC_ENTITY = f"document:skill-{SKILL_ID}"
CANONICAL_SOT_URI = (
    f"workspaces://universal-llm-gateway/.cursor/skills/{SKILL_ID}/SKILL.md"
)


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Tmp cortex DB (minimal schema) + workspace SOT skill file."""
    db_path = tmp_path / "cortex.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE entities ("
        " id TEXT PRIMARY KEY, type TEXT, name TEXT, description TEXT,"
        " attributes TEXT, source_uri TEXT)"
    )
    # Column names mirror the production schema (live_schema_snapshot.json):
    # type/from_entity/to_entity + active — NOT the API-level
    # source_id/target_id/type_id aliases.
    conn.execute(
        "CREATE TABLE relationships ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " type TEXT, from_entity TEXT, to_entity TEXT,"
        " active BOOLEAN NOT NULL DEFAULT 1)"
    )
    conn.commit()
    conn.close()

    ws_root = tmp_path / "projects"
    skill_dir = (
        ws_root / "universal-llm-gateway" / ".cursor" / "skills" / SKILL_ID
    )
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# test skill\n")

    monkeypatch.setenv("WORKSPACES_ROOT", str(ws_root))
    monkeypatch.setattr(cortex_db, "_CORTEX_DB", db_path)
    monkeypatch.setattr(ops_composites, "record", lambda *a, **k: None)

    return {
        "db_path": db_path,
        "validated_path": CANONICAL_SOT_URI,
        "skill_path": CANONICAL_SOT_URI,
    }


def _insert_skill(
    db_path: Path,
    *,
    name: str = SKILL_ID,
    description: str = "d",
    attributes: str | None = None,
    source_uri: str | None = None,
) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO entities (id, type, name, description, attributes, source_uri)"
        " VALUES (?, 'agent_skill', ?, ?, ?, ?)",
        (SKILL_ENTITY, name, description, attributes, source_uri),
    )
    conn.commit()
    conn.close()


def _stub_creates(monkeypatch: pytest.MonkeyPatch, calls: list[dict]) -> None:
    """Stub the nested create ops (they need the full production schema)."""

    def fake_entity_create(**kwargs: Any) -> dict[str, Any]:
        calls.append({"op": "entity_create", **kwargs})
        conn = sqlite3.connect(str(cortex_db._CORTEX_DB))
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, type, name) VALUES (?, ?, ?)",
            (kwargs["id"], kwargs["type"], kwargs["name"]),
        )
        conn.commit()
        conn.close()
        return {"id": kwargs["id"]}

    def fake_relationship_create(**kwargs: Any) -> dict[str, Any]:
        calls.append({"op": "relationship_create", **kwargs})
        conn = sqlite3.connect(str(cortex_db._CORTEX_DB))
        conn.execute(
            "INSERT INTO relationships (from_entity, to_entity, type)"
            " VALUES (?, ?, ?)",
            (kwargs["source_id"], kwargs["target_id"], kwargs["type_id"]),
        )
        conn.commit()
        conn.close()
        return {"id": 1}

    monkeypatch.setattr(ops_composites, "_op_entity_create", fake_entity_create)
    monkeypatch.setattr(
        ops_composites, "_op_relationship_create", fake_relationship_create
    )


def _stub_conn_creates(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[dict],
    captured: dict | None = None,
    *,
    relationship_raises: Exception | None = None,
) -> None:
    """Stub the conn-taking impls the atomic CREATE path now uses.

    The fakes write through the PASSED connection WITHOUT committing —
    mirroring the real ``commit=False`` contract — so the zero-rows /
    all-rows assertions on a fresh second connection are non-vacuous
    proofs of the composite's own COMMIT/ROLLBACK discipline.
    """

    def fake_create_entity_impl(
        conn: sqlite3.Connection, payload: dict, commit: bool = True
    ) -> dict[str, Any]:
        assert commit is False, "composite must call create_entity_impl(commit=False)"
        if captured is not None:
            captured["conn"] = conn
        calls.append({"op": "entity_create", **payload})
        conn.execute(
            "INSERT INTO entities (id, type, name) VALUES (?, ?, ?)",
            (payload["id"], payload["type"], payload["name"]),
        )
        return {"id": payload["id"]}

    def fake_create_relationship_on_conn(
        conn: sqlite3.Connection,
        body: Any,
        *,
        commit: bool = True,
        post_commit_emits: list | None = None,
    ) -> Any:
        assert commit is False, (
            "composite must call create_relationship_on_conn(commit=False)"
        )
        if relationship_raises is not None:
            raise relationship_raises
        calls.append(
            {
                "op": "relationship_create",
                "source_id": body.source_id,
                "target_id": body.target_id,
                "type_id": body.type_id,
            }
        )
        conn.execute(
            "INSERT INTO relationships (from_entity, to_entity, type)"
            " VALUES (?, ?, ?)",
            (body.source_id, body.target_id, body.type_id),
        )

        class _Result:
            was_new = True

        return _Result()

    monkeypatch.setattr(ops_composites, "create_entity_impl", fake_create_entity_impl)
    monkeypatch.setattr(
        ops_composites,
        "create_relationship_on_conn",
        fake_create_relationship_on_conn,
    )


class TestExistingUnsubstantiated:
    """The friction-22236 shape: register over an existing agent_skill row."""

    def test_matching_with_json_text_attributes_no_operational_error(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # attributes stored as JSON TEXT with unrelated keys — the exact
        # production shape (agent_skill:mcp-surface-change). Under the bug
        # this raised OperationalError("cannot rollback - no transaction is
        # active") masking an AttributeError on str.get.
        _insert_skill(
            sandbox["db_path"],
            attributes=json.dumps({"applicable_agents": ["claude-web"]}),
            source_uri=sandbox["validated_path"],
        )
        calls: list[dict] = []
        _stub_creates(monkeypatch, calls)

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
            description="d",
        )

        assert "error" not in result, result
        assert result["status"] == "existing"
        assert result["_status"] == "idempotent"
        # Substantiation backfill: document member + keystone relationship
        # were missing and must be created on the matching-existing path.
        assert DOC_ENTITY in result["backfilled_members"]
        ops = [c["op"] for c in calls]
        assert "entity_create" in ops
        assert "relationship_create" in ops

    def test_matching_backfill_skips_present_members(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _insert_skill(
            sandbox["db_path"],
            attributes=None,
            source_uri=sandbox["validated_path"],
        )
        conn = sqlite3.connect(str(sandbox["db_path"]))
        conn.execute(
            "INSERT INTO entities (id, type, name) VALUES (?, 'document', ?)",
            (DOC_ENTITY, SKILL_ID),
        )
        conn.execute(
            "INSERT INTO relationships (from_entity, to_entity, type)"
            " VALUES (?, ?, 'keystone_of')",
            (SKILL_ENTITY, DOC_ENTITY),
        )
        conn.commit()
        conn.close()
        calls: list[dict] = []
        _stub_creates(monkeypatch, calls)

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
            description="d",
        )

        assert result["status"] == "existing"
        assert result["backfilled_members"] == []
        assert calls == []

    def test_divergent_returns_conflict_with_convergent_suggested(
        self, sandbox: dict
    ) -> None:
        _insert_skill(
            sandbox["db_path"],
            name="Pretty Display Name",
            description="old",
            attributes=json.dumps({"trigger_phrases": ["x"]}),
            source_uri="workspaces://somewhere/SKILL.md",
        )

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
            description="new",
            trigger_phrases=["y"],
        )

        assert result.get("code") == "composite_conflict"
        assert result["diff"]["description"] == ["old", "new"]
        assert result["diff"]["trigger_phrases"] == [["x"], ["y"]]
        # The suggested update must cover every field the idempotency
        # equality checks, or applying it can never converge to "existing".
        updates = result["suggested"]["updates"]
        assert updates["name"] == SKILL_ID
        assert updates["source_uri"] == sandbox["validated_path"]
        assert updates["description"] == "new"
        assert updates["attributes"]["trigger_phrases"] == ["y"]

    def test_divergent_empty_request_fields_omitted_from_suggested(
        self, sandbox: dict
    ) -> None:
        """Data-loss guard (thread 4266): a probe with no description and no
        trigger_phrases must not produce a suggested update that would BLANK
        the live values via the documented apply-and-re-register ladder."""
        _insert_skill(
            sandbox["db_path"],
            name="Pretty Display Name",
            description="real live description",
            attributes=json.dumps({"trigger_phrases": ["x"]}),
            source_uri="workspaces://somewhere/SKILL.md",
        )

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
        )

        assert result.get("code") == "composite_conflict"
        # The diff is untouched — it still reports the divergence verbatim.
        assert result["diff"]["description"] == ["real live description", ""]
        assert result["diff"]["trigger_phrases"] == [["x"], []]
        updates = result["suggested"]["updates"]
        assert "description" not in updates
        assert "attributes" not in updates
        # Derived fields stay unconditional.
        assert updates["name"] == SKILL_ID
        assert updates["source_uri"] == sandbox["validated_path"]

    def test_divergent_empty_request_against_empty_current_still_included(
        self, sandbox: dict
    ) -> None:
        """Guard only fires when it would blank REAL data: empty request
        values against an empty/absent current value remain included."""
        _insert_skill(
            sandbox["db_path"],
            name="Pretty Display Name",  # divergence comes from name
            description="",
            attributes=None,
            source_uri="workspaces://somewhere/SKILL.md",
        )

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
        )

        assert result.get("code") == "composite_conflict"
        updates = result["suggested"]["updates"]
        assert updates["description"] == ""
        assert updates["attributes"]["trigger_phrases"] == []


class TestFreshCreate:
    def test_create_path_stays_green(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The atomic create path now calls the conn-taking impls directly
        # (create_entity_impl / create_relationship_on_conn) instead of the
        # _op_* wrappers — stub those.
        calls: list[dict] = []
        _stub_conn_creates(monkeypatch, calls)

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
            description="d",
            trigger_phrases=["a"],
        )

        assert result["status"] == "created"
        assert result["skill_id"] == SKILL_ENTITY
        assert result["document_id"] == DOC_ENTITY
        created_ids = [c.get("id") for c in calls if c["op"] == "entity_create"]
        assert created_ids == [SKILL_ENTITY, DOC_ENTITY]

    def test_create_path_commits_all_three_rows(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Positive companion to the injected-failure test (AC-1).

        The stubs write through the composite's connection without
        committing; only the composite's own COMMIT can make the rows
        visible to a fresh second connection.
        """
        calls: list[dict] = []
        _stub_conn_creates(monkeypatch, calls)

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
            description="d",
        )
        assert result["status"] == "created"

        conn2 = sqlite3.connect(str(sandbox["db_path"]))
        entity_count = conn2.execute(
            "SELECT COUNT(*) FROM entities WHERE id IN (?, ?)",
            (SKILL_ENTITY, DOC_ENTITY),
        ).fetchone()[0]
        rel_count = conn2.execute(
            "SELECT COUNT(*) FROM relationships"
            " WHERE from_entity = ? AND to_entity = ? AND type = 'keystone_of'",
            (SKILL_ENTITY, DOC_ENTITY),
        ).fetchone()[0]
        conn2.close()
        assert entity_count == 2
        assert rel_count == 1

    def test_injected_failure_between_writes_leaves_zero_rows(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """C1 atomicity regression (AC-1): the exact live orphan shape.

        Inject a failure BETWEEN the nested writes — after both entity
        impls have written on the composite connection, at the
        relationship step — and prove on a FRESH SECOND CONNECTION that
        zero rows survive for either entity id and for the relationship.
        """
        calls: list[dict] = []
        captured: dict = {}
        _stub_conn_creates(
            monkeypatch,
            calls,
            captured,
            relationship_raises=RuntimeError("injected mid-composite failure"),
        )

        with pytest.raises(RuntimeError, match="injected mid-composite failure"):
            ops_composites._op_register_skill_substrate(
                skill_id=SKILL_ID,
                skill_path=sandbox["skill_path"],
                description="d",
            )

        # Both entity impls ran before the injected failure.
        assert [c["op"] for c in calls] == ["entity_create", "entity_create"]

        # Fresh second connection: rollback visibility — zero orphans.
        conn2 = sqlite3.connect(str(sandbox["db_path"]))
        entity_count = conn2.execute(
            "SELECT COUNT(*) FROM entities WHERE id IN (?, ?)",
            (SKILL_ENTITY, DOC_ENTITY),
        ).fetchone()[0]
        rel_count = conn2.execute(
            "SELECT COUNT(*) FROM relationships"
            " WHERE from_entity = ? AND to_entity = ?",
            (SKILL_ENTITY, DOC_ENTITY),
        ).fetchone()[0]
        conn2.close()
        assert entity_count == 0
        assert rel_count == 0

        # Composite conn: no dangling transaction (closed is even stronger).
        try:
            assert captured["conn"].in_transaction is False
        except sqlite3.ProgrammingError:
            pass  # connection closed by the composite's finally — stronger

    def test_nested_error_returns_cleanly_without_tx_error(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The conn-taking impls raise HTTPException (they do not return
        # error dicts); the composite translates it to its outward
        # error-dict contract after rolling back.
        def boom(
            conn: sqlite3.Connection, payload: dict, commit: bool = True
        ) -> dict[str, Any]:
            raise HTTPException(status_code=422, detail="boom")

        monkeypatch.setattr(ops_composites, "create_entity_impl", boom)

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=sandbox["skill_path"],
        )

        assert result == {"error": "boom", "status_code": 422}


class TestWorkspaceSotPath:
    def test_new_registration_canonical_source_uri_is_workspace_sot(
        self, sandbox: dict, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict] = []
        _stub_conn_creates(monkeypatch, calls)

        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=f".cursor/skills/{SKILL_ID}/SKILL.md",
            description="d",
        )

        assert result["status"] == "created"
        assert result["validated_path"] == CANONICAL_SOT_URI
        skill_create = next(
            c for c in calls if c["op"] == "entity_create" and c["id"] == SKILL_ENTITY
        )
        assert skill_create["source_uri"] == CANONICAL_SOT_URI

    def test_cortex_path_rejected_with_sot_naming_error(self, sandbox: dict) -> None:
        result = ops_composites._op_register_skill_substrate(
            skill_id=SKILL_ID,
            skill_path=f"agent-skills/{SKILL_ID}.md",
        )

        assert result.get("code") == "invalid_skill_path"
        assert CANONICAL_SOT_URI in result["error"]
        assert "legacy cortex mirror" in result["error"]
        assert "todo:consolidate-skill-sot" in result["error"]
