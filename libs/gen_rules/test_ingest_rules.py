"""B2 Slice G1 tests — rule projection ingest + manifest validation."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parents[2] / "scripts" / "cortex"
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from cortex_store.routes._skill_index import body_digest  # noqa: E402

from gen_rules.agent_guides import (  # noqa: E402
    normalize_rule_entry,
    validate_rule_manifest_slugs,
)


def _mock_client(responses: dict[tuple[str, str], tuple[int, dict]]) -> MagicMock:
    client = MagicMock()

    def _request(method: str, path: str, **kwargs: object) -> MagicMock:
        key = (method, path.split("?", 1)[0])
        status, body = responses.get(key, (404, {}))
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = body
        return resp

    client.request.side_effect = _request
    return client


def test_tg7_manifest_unknown_applicable_agents_slug_fails_loud() -> None:
    bad_manifest = {
        "bogus-rule": {
            "source": "agent-surface/sources/system-conduct.md",
            "applicable_agents": ["not-a-real-seat"],
        }
    }
    with pytest.raises(SystemExit):
        validate_rule_manifest_slugs(bad_manifest)


def test_tg1_projection_upsert_and_check_passes(tmp_path: Path) -> None:
    slug = "provenance-discipline"
    rule_dir = tmp_path / "docs" / "agent-guides" / "rules"
    rule_dir.mkdir(parents=True)
    rule_file = rule_dir / f"{slug}.md"
    rule_file.write_text("# Rule\nConduct body.", encoding="utf-8")
    source_uri = f"docs/agent-guides/rules/{slug}.md"
    with patch("cortex_store.routes.boot._skill_trigger._FILES_ROOT", tmp_path):
        digest = body_digest(source_uri, slug)
    assert digest
    entry = normalize_rule_entry(
        {
            "source": "agent-surface/sources/provenance-discipline.md",
            "applicable_agents": ["*"],
        }
    )
    entity_id = f"rule:{slug}"
    projection = {
        "id": entity_id,
        "type": "rule",
        "name": slug,
        "source_uri": source_uri,
        "attributes": {
            "applicable_agents": entry["applicable_agents"],
            "digest": digest,
        },
    }
    responses: dict[tuple[str, str], tuple[int, dict]] = {
        ("GET", f"/entities/{entity_id}"): (404, {}),
        ("POST", "/entities"): (200, projection),
    }
    client = _mock_client(responses)
    manifest = {slug: entry}
    with (
        patch("ingest_rules.AGENT_GUIDES_RULE_SLUGS", manifest),
        patch("ingest_rules.make_sync_client", return_value=client),
        patch("ingest_rules.validate_rule_manifest_slugs"),
    ):
        from ingest_rules import _check, _upsert

        assert _upsert(client, projection, dry_run=False)
        live = {
            "source_uri": source_uri,
            "attributes": projection["attributes"],
        }
        responses[("GET", f"/entities/{entity_id}")] = (200, live)
        assert _check(client, tmp_path) == 0


def test_tg1_check_fails_on_digest_drift(tmp_path: Path) -> None:
    slug = "system-conduct"
    rule_dir = tmp_path / "docs" / "agent-guides" / "rules"
    rule_dir.mkdir(parents=True)
    rule_file = rule_dir / f"{slug}.md"
    rule_file.write_text("original body", encoding="utf-8")
    source_uri = f"docs/agent-guides/rules/{slug}.md"
    stale_digest = "sha256:deadbeef000000"
    entity_id = f"rule:{slug}"
    responses: dict[tuple[str, str], tuple[int, dict]] = {
        ("GET", f"/entities/{entity_id}"): (
            200,
            {
                "source_uri": source_uri,
                "attributes": {
                    "applicable_agents": ["*"],
                    "digest": stale_digest,
                },
            },
        ),
    }
    client = _mock_client(responses)
    manifest = {
        slug: {
            "source": "agent-surface/sources/system-conduct.md",
            "applicable_agents": ["*"],
        }
    }
    with (
        patch("ingest_rules.AGENT_GUIDES_RULE_SLUGS", manifest),
        patch("ingest_rules.validate_rule_manifest_slugs"),
    ):
        from ingest_rules import _check

        assert _check(client, tmp_path) == 1
