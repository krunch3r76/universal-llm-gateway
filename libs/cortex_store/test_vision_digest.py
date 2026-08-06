"""Hermetic tests for vision digest parse, route, and openapi surface."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from cortex_store.main import create_app
from cortex_store.routes.doctrine import get_vision_digest
from cortex_store.vision_digest import (
    MAP_URI,
    build_vision_digest,
    parse_map_pillars,
    resolve_map_path,
)

_FIXTURE_MAP = """\
# Posture-stack foundation map

## Pillars

### Pillar 1 — Seat-lane / life liaison (who acts, where)

**Law:** web = life-matter + adjudication liaison on the life endpoint only; code-infra routes to checkout seats (cursor / cursor-sdk / grok-on-checkout); life→code escalation = teach + bus (`lane:life-to-code`), never a new life-intent verb.

- **SoT:** `decision:seat-lane-split-liaison-model` — ADOPTED; scoreboard `cortex://notes/system/threads/4917-charter-scoreboard.md`; skills `agent_skill:life-to-code-request-lane`.
- **Child arcs must not re-decide:** dual-endpoint membership/hide posture; web dual-attach (rejected).
- **Falsifier if ignored:** a child packet hands a life seat a `workspaces://` corpus it **cannot resolve**.

**Amendment (5964).** Per attachment grammar: child arcs may cite.

> Pillar 1's surface split is a **write and membership** split.

### Pillar 2 — Imprint · ring · endpoint provenance (what persists)

**Law:** checkpoint = forward imprint (re-presented at boot); transcript/graph = archive (recalled on demand); the two check each other — never trust an imprint blind on anything material.

- **SoT:** `decision:checkpoint-imprint-thread-as-ring` + design doc `cortex://notes/system/design/checkpoint-imprint-thread-as-ring.md`.
- **Child arcs must not re-decide:** imprint-vs-megatool (F1′ closed); checkpoint/scoreboard reconstitution shape.
- **Falsifier if ignored:** a seat linear-reads a root or resumes from narrative instead of CHECKPOINT+scoreboard.

### Pillar 3 — HTTP-first · OpenAPI as Semantic-Web heir (how surfaces are reached)

**Law:** HTTP is the agent tooling substrate with **served** typed-args schemas (OpenAPI, pulled-not-pushed) — NOT "HTTP replaces MCP"; MCP remains a client adapter, not the ontology.

- **SoT:** `decision:http-first-agent-substrate`; lineage memo `workspaces://universal-llm-gateway/tasks/asymmetric-bet-portfolio/http-agent-substrate-lineage.md`; https://www.rfc-editor.org/rfc/rfc8631.
- **Child arcs must not re-decide:** HTTP-replaces-MCP (rejected framing); served-not-pushed schema posture.
- **Falsifier if ignored:** new tool families mint as untyped megatools.

### Pillar 4 — Claim-record / WWP (what may be asserted)

**Law:** a factual artifact is a rendering of a **claim record** — every material claim carries an epistemic state (`backed | unverified | gap`) × a rendering state (`express | imply | omit_with_reason`).

- **SoT:** vision `cortex://notes/system/threads/5195-fable-writing-provenance/fable-vision-dialectic.md`; detector `todo:wwp-substrate-debt-gap-detector`.
- **Child arcs must not re-decide:** r1/r2/r3; two-axis + compatibility law.
- **Falsifier if ignored:** typed frictions recur at the pre-vision rate across ≥5 WWP-governed artifacts.

### Pillar 5 — Event plane · state from events (how state propagates)

**Law:** ULG is event-driven and asynchronous in philosophy — events are the primary means of communicating and updating state; every shared state key names exactly one authority, projections derive from that authority and never co-mutate beside it.

- **SoT:** floor tags `[universal:state-provenance]`; Terra review `cortex://notes/system/reviews/2026-08-06-state-from-events-doctrine-review.md`.
- **Child arcs must not re-decide:** event-primacy as the default; the advisory-vs-fold split.
- **Falsifier if ignored:** a new correctness-bearing state holder ships that neither folds a durable domain journal nor names its recovery path.
"""

_LAW_1 = (
    "web = life-matter + adjudication liaison on the life endpoint only; "
    "code-infra routes to checkout seats (cursor / cursor-sdk / grok-on-checkout); "
    "life→code escalation = teach + bus (`lane:life-to-code`), never a new life-intent verb."
)


@pytest.fixture()
def map_root(tmp_path: Path) -> Path:
    rel = MAP_URI.removeprefix("cortex://")
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_FIXTURE_MAP, encoding="utf-8")
    return tmp_path


def test_parse_map_pillars_verbatim_law_excludes_amendment() -> None:
    pillars = parse_map_pillars(_FIXTURE_MAP)
    assert len(pillars) == 5
    assert [p.id for p in pillars] == [
        "pillar-1",
        "pillar-2",
        "pillar-3",
        "pillar-4",
        "pillar-5",
    ]
    assert pillars[0].law_verbatim == _LAW_1
    assert "Amendment" not in pillars[0].law_verbatim
    assert pillars[0].must_not_redecide[0].startswith("dual-endpoint")
    assert len(pillars[0].must_not_redecide) >= 2
    assert "cannot resolve" in pillars[0].falsifier
    assert "cortex://notes/system/threads/4917-charter-scoreboard.md" in pillars[0].sot_uris


def test_parse_map_pillars_not_capped_at_four() -> None:
    """Pillars past the original four-pillar stack still reach the digest."""
    pillars = parse_map_pillars(_FIXTURE_MAP)
    fifth = pillars[-1]
    assert fifth.id == "pillar-5"
    assert fifth.law_verbatim.startswith("ULG is event-driven and asynchronous")
    assert fifth.must_not_redecide == [
        "event-primacy as the default",
        "the advisory-vs-fold split.",
    ]
    assert "durable domain journal" in fifth.falsifier


def test_parse_map_pillars_below_floor_rejected() -> None:
    truncated = _FIXTURE_MAP.split("### Pillar 3")[0]
    with pytest.raises(ValueError, match="expected at least 4 pillars"):
        parse_map_pillars(truncated)


def test_build_vision_digest_success(map_root: Path) -> None:
    digest = build_vision_digest(map_root)
    assert digest.map_uri == MAP_URI
    assert digest.source == "live"
    assert digest.stale is False
    assert len(digest.pillars) == 5
    rel = MAP_URI.removeprefix("cortex://")
    expected = hashlib.sha256((map_root / rel).read_bytes()).hexdigest()
    assert digest.map_sha256 == f"sha256:{expected}"


def test_build_vision_digest_missing_map(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build_vision_digest(tmp_path)


def test_resolve_map_path_escape(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="outside CORTEX_FILES_ROOT"):
        resolve_map_path(tmp_path, "../../../etc/passwd")


def test_get_vision_digest_route_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cortex_store.routes.doctrine._FILES_ROOT", tmp_path)
    with pytest.raises(HTTPException) as exc:
        get_vision_digest()
    assert exc.value.status_code == 404


def test_get_vision_digest_route_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cortex_store.routes.doctrine._FILES_ROOT", tmp_path)
    monkeypatch.setattr(
        "cortex_store.routes.doctrine.build_vision_digest",
        lambda _root: (_ for _ in ()).throw(ValueError("escape")),
    )
    with pytest.raises(HTTPException) as exc:
        get_vision_digest()
    assert exc.value.status_code == 400


def test_get_vision_digest_route_200(map_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("cortex_store.routes.doctrine._FILES_ROOT", map_root)
    digest = get_vision_digest()
    assert digest.map_uri == MAP_URI
    assert digest.stale is False
    assert digest.pillars[0].law_verbatim == _LAW_1


def test_openapi_lists_vision_digest() -> None:
    client = TestClient(create_app())
    schema = client.get("/openapi.json").json()
    assert "/api/v1/doctrine/vision-digest" in schema["paths"]
    operation = schema["paths"]["/api/v1/doctrine/vision-digest"]["get"]
    assert operation["operationId"] == "getVisionDigest"
