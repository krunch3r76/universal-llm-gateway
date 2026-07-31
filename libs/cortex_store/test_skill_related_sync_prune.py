"""Unit tests for skill ``references`` edge prune + ingest edge drift."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stderr
from pathlib import Path

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parents[2] / "scripts" / "cortex"
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

import ingest_skills  # noqa: E402
from _skill_related_sync import (  # noqa: E402
    prune_stale_reference_edges,
    sync_reference_edges_only,
)


class _MockResponse:
    def __init__(self, status_code: int, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}

    def json(self) -> dict:
        return self._payload


class MockSyncClient:
    def __init__(
        self,
        *,
        edges: list[dict] | None = None,
        entities: dict[str, dict] | None = None,
    ) -> None:
        self.edges = list(edges or [])
        self.entities = dict(entities or {})
        self.calls: list[tuple[str, str, dict]] = []
        self.deleted: list[int] = []

    def request(self, method: str, path: str, **kwargs: object) -> _MockResponse:
        self.calls.append((method, path, kwargs))
        if method == "GET" and path.startswith("/relationships?"):
            entity_id = _query_param(path, "entity_id")
            type_id = _query_param(path, "type_id")
            items = [
                row
                for row in self.edges
                if row.get("active", 1)
                and row.get("type_id") == type_id
                and row.get("source_id") == entity_id
            ]
            return _MockResponse(200, {"items": items})
        if method == "DELETE" and path.startswith("/relationships/"):
            rel_id = int(path.rsplit("/", 1)[-1])
            for row in self.edges:
                if row.get("id") == rel_id:
                    row["active"] = 0
                    self.deleted.append(rel_id)
                    return _MockResponse(200, {"deleted": True, "id": rel_id})
            return _MockResponse(404, {"error": "not found"})
        if method == "GET" and path.startswith("/entities/"):
            eid = path.split("/", 2)[-1]
            eid = _url_unquote(eid)
            if eid in self.entities:
                return _MockResponse(200, self.entities[eid])
            return _MockResponse(404, {})
        if method == "POST" and path == "/relationships":
            body = kwargs.get("json") or {}
            for row in self.edges:
                if (
                    row.get("active", 1)
                    and row.get("source_id") == body.get("source_id")
                    and row.get("target_id") == body.get("target_id")
                    and row.get("type_id") == body.get("type_id")
                ):
                    return _MockResponse(200, {"was_new": False, "item": row})
            new_id = max((row.get("id", 0) for row in self.edges), default=0) + 1
            row = {
                "id": new_id,
                "source_id": body["source_id"],
                "target_id": body["target_id"],
                "type_id": body["type_id"],
                "active": 1,
            }
            self.edges.append(row)
            return _MockResponse(201, {"was_new": True, "item": row})
        if method == "PATCH" and path.startswith("/entities/"):
            eid = _url_unquote(path.split("/", 2)[-1])
            body = kwargs.get("json") or {}
            live = self.entities.setdefault(eid, {"id": eid, "attributes": {}})
            if "attributes" in body:
                attrs = dict(live.get("attributes") or {})
                attrs.update(body["attributes"])
                live["attributes"] = attrs
            return _MockResponse(200, live)
        return _MockResponse(404, {})


def _query_param(path: str, key: str) -> str | None:
    if "?" not in path:
        return None
    query = path.split("?", 1)[1]
    for part in query.split("&"):
        k, _, v = part.partition("=")
        if k == key:
            return _url_unquote(v)
    return None


def _url_unquote(value: str) -> str:
    from urllib.parse import unquote

    return unquote(value)


from _skill_related_parse import resolve_related_target_id  # noqa: E402


@pytest.mark.offline
@pytest.mark.parametrize(
    ("declared", "entity_id"),
    [
        ("implement-todo", "agent_skill:implement-todo"),
        ("rule:todo-lifecycle", "rule:todo-lifecycle"),
        ("agent_skill:foo", "agent_skill:foo"),
        ("agent_skill:rule:todo-lifecycle", "rule:todo-lifecycle"),
    ],
)
def test_resolve_related_target_id(declared: str, entity_id: str) -> None:
    assert resolve_related_target_id(declared) == entity_id


def test_prune_stale_reference_edges_soft_deletes() -> None:
    client = MockSyncClient(
        edges=[
            {
                "id": 1,
                "source_id": "agent_skill:foo",
                "target_id": "agent_skill:keep",
                "type_id": "references",
                "active": 1,
            },
            {
                "id": 2,
                "source_id": "agent_skill:foo",
                "target_id": "agent_skill:stale",
                "type_id": "references",
                "active": 1,
            },
        ]
    )
    assert prune_stale_reference_edges(client, "foo", ["keep"], dry_run=False)
    assert client.deleted == [2]
    assert client.edges[1]["active"] == 0


def test_prune_dry_run_prints_would_retire_and_keeps_edge(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = MockSyncClient(
        edges=[
            {
                "id": 9,
                "source_id": "agent_skill:foo",
                "target_id": "agent_skill:stale",
                "type_id": "references",
                "active": 1,
            }
        ]
    )
    assert prune_stale_reference_edges(client, "foo", [], dry_run=True)
    assert client.deleted == []
    assert "WOULD-RETIRE" in capsys.readouterr().out
    assert client.edges[0]["active"] == 1


def test_prune_skips_non_agent_skill_target() -> None:
    client = MockSyncClient(
        edges=[
            {
                "id": 3,
                "source_id": "agent_skill:foo",
                "target_id": "doc:other",
                "type_id": "references",
                "active": 1,
            }
        ]
    )
    assert prune_stale_reference_edges(client, "foo", [], dry_run=False)
    assert client.deleted == []
    assert client.edges[0]["active"] == 1


def test_sync_reference_edges_only_creates_missing_and_prunes_stale() -> None:
    client = MockSyncClient(
        edges=[
            {
                "id": 4,
                "source_id": "agent_skill:src",
                "target_id": "agent_skill:old",
                "type_id": "references",
                "active": 1,
            }
        ],
        entities={
            "agent_skill:src": {"id": "agent_skill:src", "attributes": {}},
            "agent_skill:new": {"id": "agent_skill:new", "attributes": {}},
            "agent_skill:old": {"id": "agent_skill:old", "attributes": {}},
        },
    )
    assert sync_reference_edges_only(client, "src", ["new"], dry_run=False)
    assert client.deleted == [4]
    active_targets = {
        row["target_id"]
        for row in client.edges
        if row.get("active", 1) and row["source_id"] == "agent_skill:src"
    }
    assert active_targets == {"agent_skill:new"}


def test_sync_reference_edges_only_creates_typed_rule_target() -> None:
    client = MockSyncClient(
        entities={
            "agent_skill:todo-lifecycle": {
                "id": "agent_skill:todo-lifecycle",
                "attributes": {},
            },
            "rule:todo-lifecycle": {
                "id": "rule:todo-lifecycle",
                "attributes": {},
            },
        }
    )
    assert sync_reference_edges_only(
        client,
        "todo-lifecycle",
        ["rule:todo-lifecycle"],
        dry_run=False,
    )
    active_targets = {
        row["target_id"]
        for row in client.edges
        if row.get("active", 1) and row["source_id"] == "agent_skill:todo-lifecycle"
    }
    assert active_targets == {"rule:todo-lifecycle"}


def test_reference_edge_drift_detects_stale_edge_only() -> None:
    client = MockSyncClient(
        edges=[
            {
                "id": 5,
                "source_id": "agent_skill:src",
                "target_id": "agent_skill:stale",
                "type_id": "references",
                "active": 1,
            }
        ],
        entities={
            "agent_skill:src": {
                "id": "agent_skill:src",
                "attributes": {"related_skills": []},
            }
        },
    )
    drifts = ingest_skills._reference_edge_drift(client, "src", [])
    assert len(drifts) == 1
    assert "stale references edge" in drifts[0]


def test_ingest_slug_unknown_exits_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MockSyncClient()
    monkeypatch.setattr(ingest_skills, "make_sync_client", lambda _url: client)
    monkeypatch.setattr(ingest_skills, "_scan_skills", lambda _root: {})
    monkeypatch.setattr(ingest_skills, "_scan_cortex_sot_metadata", lambda: {})
    monkeypatch.setattr(ingest_skills, "_scan_cortex_sot_declared", lambda: {})
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = ingest_skills.main(["--slug", "missing-skill"])
    assert code == 2
    assert "unknown skill slug" in stderr.getvalue()


def test_ingest_slug_invalid_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    stderr = io.StringIO()
    with redirect_stderr(stderr):
        code = ingest_skills.main(["--slug", "Bad Slug"])
    assert code == 2
    assert "invalid slug" in stderr.getvalue()


def test_prune_does_not_touch_related_to_edges() -> None:
    client = MockSyncClient(
        edges=[
            {
                "id": 6,
                "source_id": "agent_skill:build-pipeline",
                "target_id": "agent_skill:refine-pipeline",
                "type_id": "related_to",
                "active": 1,
            }
        ]
    )
    assert prune_stale_reference_edges(client, "build-pipeline", [], dry_run=False)
    assert client.deleted == []
    assert client.edges[0]["active"] == 1
