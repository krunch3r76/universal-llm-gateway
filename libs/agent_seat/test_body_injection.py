"""G3 inline-only body injection tests (T1–T17)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent_seat.body_injection import (
    INJECTED_BODY_BUDGET_BYTES,
    RequiredBodyUnresolved,
    build_injected_bodies_md,
    clear_payload_cache_for_tests,
    resolve_inline_only_bodies,
)
from agent_seat.prompts import assemble_system_prompt
from gen_rules.agent_guides import AGENT_GUIDES_RULE_SLUGS

_REPO = Path(__file__).resolve().parents[2]


def _entry(
    entity_id: str,
    *,
    name: str | None = None,
    digest: str = "sha256:abc",
    body: str = "body",
    delivery_priority: int = 100,
    delivery_criticality: str | None = None,
) -> dict[str, Any]:
    return {
        "id": entity_id,
        "name": name or entity_id.split(":", 1)[-1],
        "digest": digest,
        "body": body,
        "delivery_priority": delivery_priority,
        "delivery_criticality": delivery_criticality,
    }


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    clear_payload_cache_for_tests()


def test_assemble_injects_bodies_part() -> None:
    injected = "<!-- injected-body:rule:foo digest:sha256:abc -->"
    system = assemble_system_prompt(
        "gatherer",
        briefing_card_md="# Briefing",
        continuation_md="# Continuation",
        injected_bodies_md=injected,
    )
    assert (
        system.index("Briefing") < system.index(injected) < system.index("Continuation")
    )


def test_assemble_is_io_free(monkeypatch: pytest.MonkeyPatch) -> None:
    client = MagicMock()
    monkeypatch.setattr(
        "agent_seat.prompts.make_sync_client",
        lambda *a, **k: client,
        raising=False,
    )
    assemble_system_prompt("gatherer", injected_bodies_md="X")
    client.get.assert_not_called()


def test_resolve_covers_rules_and_invariants(monkeypatch: pytest.MonkeyPatch) -> None:
    index = [
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:inv",
            "delivery_priority": 0,
        },
        {
            "id": "rule:system-conduct",
            "name": "system-conduct",
            "digest": "sha256:rule",
            "delivery_priority": 100,
        },
    ]
    bodies = {
        ("agent_skill:architecture-invariants", "sha256:inv"): "invariant body",
        ("rule:system-conduct", "sha256:rule"): "rule body",
    }

    def fake_index(seat: str, layer: str = "all", **_: Any) -> list[dict[str, Any]]:
        assert layer == "all"
        return index

    def fake_body(
        entity_id: str, expected_digest: str | None, **_: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        body = bodies.get((entity_id, expected_digest or ""))
        if body is None:
            return None, "body_missing"
        return {"body": body, "digest": expected_digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    block, injected, _, _ = resolve_inline_only_bodies("grok-api-multi")
    assert "invariant body" in block
    assert "rule body" in block
    assert len(injected) == 2


def test_priority_by_delivery_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    index = [
        {
            "id": "rule:zzz-late",
            "name": "aaa-name",
            "digest": "sha256:z",
            "delivery_priority": 50,
        },
        {
            "id": "rule:aaa-early",
            "name": "zzz-name",
            "digest": "sha256:a",
            "delivery_priority": 200,
        },
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:i0",
            "delivery_priority": 0,
        },
        {
            "id": "agent_skill:ulg-architecture",
            "name": "ulg-architecture",
            "digest": "sha256:i1",
            "delivery_priority": 1,
        },
    ]
    order: list[str] = []

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def fake_body(entity_id: str, digest: str | None, **_: Any) -> tuple[Any, Any]:
        order.append(entity_id)
        return {"body": f"body-{entity_id}", "digest": digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    resolve_inline_only_bodies("grok-api-multi", budget_bytes=1_000_000)
    assert order[0] == "agent_skill:architecture-invariants"
    assert order[1] == "agent_skill:ulg-architecture"
    assert order[2] == "rule:zzz-late"
    assert order[3] == "rule:aaa-early"


def test_continue_after_drop() -> None:
    huge = "x" * 20_000
    small = "y" * 100
    entries = [
        _entry("rule:heavy", body=huge, delivery_priority=200, digest="sha256:h"),
        _entry("rule:light", body=small, delivery_priority=0, digest="sha256:l"),
    ]
    block, injected, dropped = build_injected_bodies_md(
        "grok-api-multi",
        entries,
        budget_bytes=5000,
    )
    assert "y" * 100 in block
    assert "x" * 20_000 not in block
    assert any(d["id"] == "rule:heavy" and d["reason"] == "budget" for d in dropped)
    assert any(i["id"] == "rule:light" for i in injected)


def test_body_fetch_passes_expected_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return [
            {
                "id": "rule:drift",
                "name": "drift",
                "digest": "sha256:expected",
                "delivery_priority": 0,
            }
        ]

    def fake_body(
        entity_id: str, expected_digest: str | None, **_: Any
    ) -> tuple[dict[str, Any] | None, str | None]:
        seen.append(expected_digest)
        return None, "digest_mismatch"

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    _, _, dropped, _ = resolve_inline_only_bodies("grok-api-multi")
    assert seen == ["sha256:expected"]
    assert dropped == [{"id": "rule:drift", "reason": "digest_mismatch"}]


def test_fail_open_on_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_seat.body_injection._fetch_skill_index_sync",
        lambda *a, **k: [],
    )
    block, injected, dropped, metrics = resolve_inline_only_bodies("grok-api-multi")
    assert block == ""
    assert injected == []
    assert dropped[0]["reason"] == "unreachable"
    assert "elapsed_ms" in metrics


def test_required_criticality_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "agent_seat.body_injection._fetch_skill_index_sync",
        lambda *a, **k: [
            {
                "id": "rule:critical",
                "name": "critical",
                "digest": "sha256:c",
                "delivery_priority": 0,
                "delivery_criticality": "required",
            }
        ],
    )
    monkeypatch.setattr(
        "agent_seat.body_injection._fetch_body_sync",
        lambda *a, **k: (None, "body_missing"),
    )
    with pytest.raises(RequiredBodyUnresolved):
        resolve_inline_only_bodies("grok-api-multi")


def test_payload_cache_keyed_by_id_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    index = [
        {
            "id": "rule:cached",
            "name": "cached",
            "digest": "sha256:v1",
            "delivery_priority": 0,
        }
    ]

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def fake_body(entity_id: str, digest: str | None, **_: Any) -> tuple[Any, Any]:
        calls.append(f"{entity_id}:{digest}")
        return {"body": "payload", "digest": digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    resolve_inline_only_bodies("grok-api-multi")
    assert calls == ["rule:cached:sha256:v1"]
    resolve_inline_only_bodies("grok-api-multi")
    assert calls == ["rule:cached:sha256:v1"]

    index[0]["digest"] = "sha256:v2"
    resolve_inline_only_bodies("grok-api-multi")
    assert calls == [
        "rule:cached:sha256:v1",
        "rule:cached:sha256:v2",
    ]


def test_dedup_matches_marker_not_raw_digest() -> None:
    digest = "sha256:deadbeef"
    entries = [_entry("rule:foo", digest=digest, body="inject me")]
    raw_present = f"index shows digest {digest} without marker"
    block_raw, injected_raw, _ = build_injected_bodies_md(
        "seat", entries, already_present=raw_present
    )
    assert injected_raw
    assert digest in block_raw

    marker_present = f"<!-- injected-body:foo digest:{digest} -->"
    block_marker, injected_marker, _ = build_injected_bodies_md(
        "seat", entries, already_present=marker_present
    )
    assert injected_marker == []
    assert block_marker == ""


def test_cache_output_differs_when_already_present_differs() -> None:
    entries = [_entry("rule:foo", digest="sha256:same", body="same body")]
    block_a, _, _ = build_injected_bodies_md("seat", entries, already_present="")
    block_b, _, _ = build_injected_bodies_md(
        "seat",
        entries,
        already_present="<!-- injected-body:foo digest:sha256:same -->",
    )
    assert block_a != block_b
    assert block_b == ""


def test_total_deadline_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    index = [
        {
            "id": f"rule:{i}",
            "name": f"r{i}",
            "digest": f"sha256:{i}",
            "delivery_priority": i,
        }
        for i in range(5)
    ]

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def slow_body(*_: Any, **__: Any) -> tuple[Any, Any]:
        time.sleep(0.05)
        return {"body": "b", "digest": "sha256:0"}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", slow_body)

    _, _, dropped, metrics = resolve_inline_only_bodies(
        "grok-api-multi",
        total_deadline_ms=60,
    )
    assert metrics["deadline_hit"] is True
    assert any(d["reason"] == "timeout" for d in dropped)


def test_launch_corpus_survivor_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """T15 — real 11-rule + 2-skill corpus exceeds default budget."""
    index: list[dict[str, Any]] = [
        {
            "id": "agent_skill:architecture-invariants",
            "name": "architecture-invariants",
            "digest": "sha256:inv0",
            "delivery_priority": 0,
        },
        {
            "id": "agent_skill:ulg-architecture",
            "name": "ulg-architecture",
            "digest": "sha256:inv1",
            "delivery_priority": 1,
        },
    ]
    bodies: dict[str, str] = {}
    for slug in sorted(AGENT_GUIDES_RULE_SLUGS):
        path = _REPO / "docs" / "agent-guides" / "rules" / f"{slug}.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else f"# {slug}\n"
        bodies[f"rule:{slug}"] = text
        index.append(
            {
                "id": f"rule:{slug}",
                "name": slug,
                "digest": f"sha256:{slug}",
                "delivery_priority": 100,
            }
        )
    for inv in ("architecture-invariants", "ulg-architecture"):
        path = _REPO / "docs" / "agent-guides" / "skills" / f"{inv}.md"
        bodies[f"agent_skill:{inv}"] = (
            path.read_text(encoding="utf-8") if path.is_file() else f"# {inv}\n"
        )

    def fake_index(*_: Any, **__: Any) -> list[dict[str, Any]]:
        return index

    def fake_body(entity_id: str, digest: str | None, **_: Any) -> tuple[Any, Any]:
        return {"body": bodies[entity_id], "digest": digest}, None

    monkeypatch.setattr("agent_seat.body_injection._fetch_skill_index_sync", fake_index)
    monkeypatch.setattr("agent_seat.body_injection._fetch_body_sync", fake_body)

    _, injected, dropped, _ = resolve_inline_only_bodies(
        "grok-api-multi",
        budget_bytes=INJECTED_BODY_BUDGET_BYTES,
    )
    injected_ids = [i["id"] for i in injected]
    assert injected_ids[0] == "agent_skill:architecture-invariants"
    assert injected_ids[1] == "agent_skill:ulg-architecture"
    running = 0
    expected: list[str] = []
    sorted_rows = sorted(
        index,
        key=lambda r: (
            r.get("delivery_priority")
            if r.get("delivery_priority") is not None
            else 100,
            str(r.get("name")),
        ),
    )
    for row in sorted_rows:
        eid = row["id"]
        slug = row["name"]
        digest = row["digest"]
        body = bodies[eid]
        size = len(
            f"\n\n<!-- injected-body:{slug} digest:{digest} -->\n```markdown\n{body}\n```"
        )
        if running + size <= INJECTED_BODY_BUDGET_BYTES:
            running += size
            expected.append(eid)
        else:
            break
    assert injected_ids == expected
    assert any(d.get("reason") == "budget" for d in dropped)
