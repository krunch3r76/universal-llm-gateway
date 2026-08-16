"""Unit tests for CHECKPOINT body projection."""

from __future__ import annotations

import pytest
from agent_bus_store.checkpoint_citation_lint import CitationToken
from agent_bus_store.checkpoint_projection import (
    CANONICAL_RESUME_FOOTER,
    ArtifactAnchor,
    CheckpointBodyTooLargeError,
    ChildThreadRow,
    EntityAssertionRow,
    ProjectionResolvers,
    authored_residue_char_count,
    extract_authored_residue,
    project_checkpoint_body,
)
from agent_bus_store.checkpoint_projection_wiring import maybe_project_checkpoint_body
from agent_bus_store.turns_models import MAX_TURN_BODY_CHARS


def _resolvers(
    *,
    children: tuple[ChildThreadRow, ...] = (),
    anchors: dict[str, ArtifactAnchor] | None = None,
    rows: dict[tuple[str, str], EntityAssertionRow] | None = None,
    child_raises: bool = False,
    artifact_raises: bool = False,
    row_raises: bool = False,
) -> ProjectionResolvers:
    anchor_map = anchors or {}
    row_map = rows or {}

    def _child_registry(
        *, root_thread: str, cited_thread_ids: tuple[str, ...]
    ) -> tuple[tuple[ChildThreadRow, ...], tuple[ChildThreadRow, ...]]:
        if child_raises:
            raise RuntimeError("bus unreachable")
        lookup = {row.thread_id: row for row in children}
        cited = tuple(
            lookup[tid]
            for tid in cited_thread_ids
            if tid in lookup and tid != root_thread
        )
        substantiated = tuple(
            row for row in children if row.thread_id not in cited_thread_ids
        )
        if substantiated:
            return substantiated, cited
        return (), cited

    def _artifact_sha(uri: str) -> ArtifactAnchor | None:
        if artifact_raises:
            raise RuntimeError("fs unreachable")
        return anchor_map.get(uri)

    def _citation_row(token: CitationToken) -> EntityAssertionRow | None:
        if row_raises:
            raise RuntimeError("graph unreachable")
        return row_map.get((token.kind, token.identifier))

    return ProjectionResolvers(
        child_registry=_child_registry,
        artifact_sha=_artifact_sha,
        citation_row=_citation_row,
    )


def test_registry_rendering() -> None:
    residue = "Child work tracked on agent-bus:6357 and agent-bus:6341."
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(
            children=(
                ChildThreadRow("6357", "active", 12),
                ChildThreadRow("6341", "closed", 19),
            )
        ),
    )
    assert "### Child lanes" in body
    assert "### Cited lanes" in body
    assert "agent-bus:6357 · unassociated · active · turn 12" in body
    assert "agent-bus:6341" not in body.split("### Cited lanes")[1].split("### Artifact")[0]
    assert CANONICAL_RESUME_FOOTER in body


def test_root_id_not_rendered_as_own_child() -> None:
    residue = "See agent-bus:6341 for continuity."
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(
            children=(ChildThreadRow("6341", "active", 3),)
        ),
    )
    assert "### Child lanes" in body
    assert "_none substantiated_" in body
    assert "_none cited_" in body
    assert "agent-bus:6341 · active · turn 3" not in body


def test_grandchild_not_child_of_root() -> None:
    child = ChildThreadRow(
        "7188",
        "active",
        12,
        lane_role="sub_mission",
        parent_thread_id="7182",
    )
    grandchild = ChildThreadRow(
        "7197",
        "active",
        4,
        lane_role="spillover",
        parent_thread_id="7188",
    )

    def _child_registry(
        *, root_thread: str, cited_thread_ids: tuple[str, ...]
    ) -> tuple[tuple[ChildThreadRow, ...], tuple[ChildThreadRow, ...]]:
        del cited_thread_ids
        assert root_thread == "7182"
        return (child,), (grandchild,)

    body = project_checkpoint_body(
        root_thread="7182",
        residue="agent-bus:7188 and agent-bus:7197",
        resolvers=ProjectionResolvers(
            child_registry=_child_registry,
            artifact_sha=lambda uri: None,
            citation_row=lambda token: None,
        ),
    )
    assert "agent-bus:7188 · sub_mission · active · turn 12" in body
    assert (
        "agent-bus:7197 · spillover of agent-bus:7188 · active · turn 4" in body
    )
    assert body.index("### Child lanes") < body.index("### Cited lanes")


def test_snippet_and_staleness_flags() -> None:
    residue = "Anchor a:27033 on todo:spec-v0."
    row = EntityAssertionRow(
        row_id="a:27033",
        entity="todo:spec-v0",
        claim_head="Over-capture anchor for phantom files_deleted",
        confidence=0.97,
        superseded_by="a:28000",
        valid_until="2026-08-01T00:00:00Z",
        newer_on_entity=True,
    )
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(rows={("assertion", "27033"): row}),
    )
    assert "a:27033 · todo:spec-v0" in body
    assert "Over-capture anchor" in body
    assert "conf=0.97" in body
    assert "superseded_by=a:28000" in body
    assert "valid_until=2026-08-01T00:00:00Z" in body
    assert "newer_assertion_on_entity" in body


def test_closed_child_compression_rule() -> None:
    children = tuple(
        ChildThreadRow(str(7000 + i), "closed", i + 1) for i in range(120)
    )
    cited = " ".join(f"agent-bus:{7000 + i}" for i in range(120))
    residue = cited + "\n" + ("z" * 2200)
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(children=children),
    )
    assert len(body) <= MAX_TURN_BODY_CHARS
    assert "agent-bus:7000 closed@1" in body
    assert " · closed · turn " not in body


def test_spill_guard_raises_when_compression_insufficient() -> None:
    residue = "x" * (MAX_TURN_BODY_CHARS + 500)
    with pytest.raises(CheckpointBodyTooLargeError) as exc:
        project_checkpoint_body(
            root_thread="6341",
            residue=residue,
            resolvers=_resolvers(),
        )
    assert exc.value.envelope["code"] == "checkpoint_body_too_large"


def test_unprojected_fail_open_banner() -> None:
    residue = "See a:1 and cortex://notes/system/specs/foo.md"
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(
            child_raises=True,
            artifact_raises=True,
            row_raises=True,
        ),
    )
    assert "**UNPROJECTED**" in body
    assert CANONICAL_RESUME_FOOTER in body


def test_missing_referents_do_not_stamp_unprojected() -> None:
    """CCL-4: None/missing ≠ unreachable — omit row, keep tip verified."""
    uri = "cortex://notes/system/specs/missing.md"
    residue = f"See a:99999 and `{uri}`"
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(),  # all resolvers return None
    )
    assert "**UNPROJECTED**" not in body
    assert f"{uri} · unresolved" in body


def test_backtick_wrapped_uri_yields_artifact_sha() -> None:
    uri = "cortex://notes/system/threads/6341-handoff-2026-07-29.md"
    residue = f"Handoff: `{uri}`"
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(
            anchors={uri: ArtifactAnchor(uri=uri, sha256="deadbeef")}
        ),
    )
    assert f"{uri} · sha256:deadbeef" in body
    assert "**UNPROJECTED**" not in body
    assert "unresolved" not in body


def test_extract_authored_residue_strips_derived_and_footer() -> None:
    full = (
        "## Derived (projected at post — do not hand-edit)\n"
        "derived content\n\n"
        "## Residue (authored — cap ~800 chars)\n"
        "only this remains\n\n"
        f"{CANONICAL_RESUME_FOOTER}"
    )
    assert extract_authored_residue(full) == "only this remains"
    assert authored_residue_char_count(full) == len("only this remains")


def test_maybe_project_checkpoint_subject_gate() -> None:
    plain = "hello"
    assert (
        maybe_project_checkpoint_body(
            thread="6341", subject="status update", body=plain
        )
        == plain
    )
    projected = maybe_project_checkpoint_body(
        thread="6341",
        subject="CHECKPOINT — wave 2",
        body="Settled: nothing new.",
    )
    assert "## Derived (projected at post" in projected
    assert CANONICAL_RESUME_FOOTER in projected


def test_artifact_anchor_rendering() -> None:
    uri = "cortex://notes/system/specs/checkpoint-schema-profiles.md"
    residue = f"Spec lives at {uri}"
    body = project_checkpoint_body(
        root_thread="6341",
        residue=residue,
        resolvers=_resolvers(
            anchors={uri: ArtifactAnchor(uri=uri, sha256="abc123")}
        ),
    )
    assert f"{uri} · sha256:abc123" in body


def test_post_turn_checkpoint_projection_route(tmp_path, monkeypatch) -> None:
    from unittest.mock import patch

    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from fastapi.testclient import TestClient

    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None

    residue = "Settled: projector wired."
    projected = (
        "## Derived (projected at post — do not hand-edit)\n"
        "derived\n\n"
        f"## Residue (authored — cap ~800 chars)\n{residue}\n\n"
        "— RESUME (any seat, no command): load checkpoint-discipline"
    )

    with patch(
        "agent_bus_store.routes.turns.maybe_project_checkpoint_body",
        side_effect=lambda *, thread, subject, body: (
            projected if subject.upper().startswith("CHECKPOINT") else body
        ),
    ) as projector:
        with TestClient(app) as client:
            seed = client.post(
                "/threads/with-turn",
                json={
                    "slug": "cp-route",
                    "from": "cursor",
                    "to": "web",
                    "subject": "seed",
                    "body": "hello",
                },
            )
            assert seed.status_code == 201, seed.text
            thread_id = seed.json()["thread"]["id"]
            resp = client.post(
                "/turns",
                json={
                    "thread": thread_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "CHECKPOINT — route test",
                    "body": residue,
                    "after_turn": 1,
                },
            )
            assert resp.status_code == 201, resp.text
            projector.assert_called_once()
            turn = client.get(
                f"/turns/by-number?thread={thread_id}&turn_number=2"
            ).json()
            assert turn["body"] == projected

            plain = client.post(
                "/turns",
                json={
                    "thread": thread_id,
                    "from": "cursor",
                    "to": "web",
                    "subject": "plain status",
                    "body": "no projection",
                    "after_turn": 2,
                },
            )
            assert plain.status_code == 201, plain.text
            plain_turn = client.get(
                f"/turns/by-number?thread={thread_id}&turn_number=3"
            ).json()
            assert plain_turn["body"] == "no projection"
            assert projector.call_count == 2


def test_child_registry_uses_live_lineage_primitive(tmp_path, monkeypatch) -> None:
    """G4: `_child_registry`'s substantiated bucket is backed by the real,
    unmocked `get_thread_lineage` (G2) primitive — a real `lane_bind` child
    shows up in a real posted CHECKPOINT's `### Child lanes` section, with no
    patching of `maybe_project_checkpoint_body` or the resolver wiring.
    """
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from fastapi.testclient import TestClient

    cortex_root = tmp_path / "cortex-files"
    cortex_root.mkdir()
    monkeypatch.setenv("CORTEX_FILES_ROOT", str(cortex_root))
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None

    with TestClient(app) as client:
        parent = client.post(
            "/threads/with-turn",
            json={
                "slug": "g4-parent",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert parent.status_code == 201, parent.text
        parent_id = parent.json()["thread"]["id"]

        child = client.post(
            "/threads/with-turn",
            json={
                "slug": "g4-child",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert child.status_code == 201, child.text
        child_id = child.json()["thread"]["id"]

        bind = client.post(
            f"/threads/{child_id}/lane-bind",
            json={"parent_thread_id": parent_id, "lane_role": "sub_mission"},
        )
        assert bind.status_code == 200, bind.text

        checkpoint = client.post(
            "/turns",
            json={
                "thread": parent_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT — G4 live-lineage wiring test",
                "body": "Settled: G4 refactor lands.",
                "after_turn": 1,
            },
        )
        assert checkpoint.status_code == 201, checkpoint.text

        posted = client.get(
            f"/turns/by-number?thread={parent_id}&turn_number=2"
        ).json()
        body = posted["body"]

    assert "### Child lanes" in body
    assert f"agent-bus:{child_id} · sub_mission · active · turn 1" in body
    assert "_none substantiated_" not in body


def test_child_registry_survives_dispatch_link_io_failure(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G9: substantiated child lanes render when dispatch-link load raises."""
    from agent_bus_store import create_app
    from agent_bus_store.auth import require_token
    from agent_bus_store.db import lineage as lineage_db
    from fastapi.testclient import TestClient

    def _boom(conn, thread_id: str):
        raise RuntimeError("dispatch-link I/O unavailable")

    monkeypatch.setattr(lineage_db, "load_dispatch_links", _boom)
    monkeypatch.setenv("AGENT_BUS_DB_PATH", str(tmp_path / "bus.db"))
    app = create_app(db_path=str(tmp_path / "bus.db"))
    app.dependency_overrides[require_token] = lambda: None

    with TestClient(app) as client:
        parent = client.post(
            "/threads/with-turn",
            json={
                "slug": "g9-parent",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert parent.status_code == 201, parent.text
        parent_id = parent.json()["thread"]["id"]

        child = client.post(
            "/threads/with-turn",
            json={
                "slug": "g9-child",
                "from": "cursor",
                "to": "web",
                "subject": "seed",
                "body": "hello",
            },
        )
        assert child.status_code == 201, child.text
        child_id = child.json()["thread"]["id"]

        bind = client.post(
            f"/threads/{child_id}/lane-bind",
            json={"parent_thread_id": parent_id, "lane_role": "sub_mission"},
        )
        assert bind.status_code == 200, bind.text

        checkpoint = client.post(
            "/turns",
            json={
                "thread": parent_id,
                "from": "cursor",
                "to": "web",
                "subject": "CHECKPOINT — G9 dispatch-link failure isolation",
                "body": "Settled: G9 lands.",
                "after_turn": 1,
            },
        )
        assert checkpoint.status_code == 201, checkpoint.text

        posted = client.get(
            f"/turns/by-number?thread={parent_id}&turn_number=2"
        ).json()
        body = posted["body"]

    assert "### Child lanes" in body
    assert f"agent-bus:{child_id} · sub_mission · active · turn 1" in body
    assert "_none substantiated_" not in body

