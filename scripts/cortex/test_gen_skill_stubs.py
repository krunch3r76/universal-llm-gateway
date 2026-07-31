"""Tests for gen_skill_stubs steady-state manifest contract."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS_CORTEX = Path(__file__).resolve().parent
_REPO = _SCRIPTS_CORTEX.parent.parent
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_constants import (  # noqa: E402
    GENERATOR_VERSION,
    REMEDIATION_CMD,
    RENDERER_INPUT_FIELDS,
    normalize_slug,
    slug_to_name,
)
from _skill_manifest import (  # noqa: E402
    compute_output_tree_hash,
    compute_sot_snapshot_hash,
    manifest_path,
    read_manifest,
    verify_manifest,
    write_manifest,
)
from _skill_render import (  # noqa: E402
    extract_renderer_fields,
    render_stub,
)
from gen_skill_stubs import (  # noqa: E402
    _sot_drift_verdict,
    run_check,
    run_generate,
    run_verify_manifest,
    skill_graph_staleness_cue,
)


def _entity(
    slug: str,
    *,
    description: str = "Test skill description.",
    terms: list[str] | None = None,
    related: list[str] | None = None,
    source_uri: str = "workspaces://universal-llm-gateway/.cursor/skills/example/SKILL.md",
    pointer: str = "",
) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "applicable_agents": ["*"],
        "trigger_match_terms": terms or [slug, slug.replace("-", "_")],
    }
    if related is not None:
        attrs["related_skills"] = related
    if pointer:
        attrs["paired_rule_pointer"] = pointer
    return {
        "id": f"agent_skill:{slug}",
        "type": "agent_skill",
        "lifecycle": "active",
        "discoverable": True,
        "description": description,
        "source_uri": source_uri,
        "aliases": None,
        "attributes": attrs,
        "relationships": [],
    }


def _mock_client(entities: dict[str, dict[str, Any]]) -> MagicMock:
    client = MagicMock()

    def request(method: str, path: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        if method == "GET" and path.startswith("/entities?"):
            resp.status_code = 200
            resp.json.return_value = {
                "items": [{"id": f"agent_skill:{slug}"} for slug in sorted(entities)]
            }
            return resp
        if method == "GET" and path.startswith("/entities/"):
            entity_path = path.removeprefix("/entities/").split("?", 1)[0]
            slug = entity_path.removeprefix("agent_skill:")
            resp.status_code = 200
            resp.json.return_value = entities[slug]
            return resp
        resp.status_code = 404
        resp.json.return_value = {}
        return resp

    client.request.side_effect = request
    return client


@pytest.mark.offline
def test_slug_normalization_shared() -> None:
    assert normalize_slug(" Foo-Bar ") == "foo-bar"
    assert slug_to_name("foo-bar") == "Foo Bar"


@pytest.mark.offline
def test_renderer_is_deterministic_and_has_header() -> None:
    fields = extract_renderer_fields(_entity("demo-skill"), "demo-skill")
    first = render_stub("demo-skill", fields)
    second = render_stub("demo-skill", fields)
    assert first == second
    assert "GENERATED — DO NOT EDIT" in first
    assert f'generator_version: "{GENERATOR_VERSION}"' in first
    assert "checked_at" not in first


@pytest.mark.offline
def test_renderer_input_closure_non_hashed_field(tmp_path: Path) -> None:
    entity = _entity("closure-skill")
    base_fields = extract_renderer_fields(entity, "closure-skill")
    base_stub = render_stub("closure-skill", base_fields)
    mutated = dict(entity)
    mutated["notes"] = "does not affect renderer"
    mutated_fields = extract_renderer_fields(mutated, "closure-skill")
    assert render_stub("closure-skill", mutated_fields) == base_stub
    base_hash = compute_sot_snapshot_hash([("closure-skill", base_fields)])
    assert compute_sot_snapshot_hash([("closure-skill", mutated_fields)]) == base_hash

    mutated_fields = dict(base_fields)
    mutated_fields["description"] = "Changed description text."
    assert render_stub("closure-skill", mutated_fields) != base_stub
    assert compute_sot_snapshot_hash([("closure-skill", mutated_fields)]) != base_hash


@pytest.mark.offline
def test_renderer_field_set_matches_constant() -> None:
    fields = extract_renderer_fields(_entity("field-set"), "field-set")
    assert tuple(fields.keys()) == tuple(RENDERER_INPUT_FIELDS)


@pytest.mark.offline
def test_verify_manifest_fails_closed_with_remediation(tmp_path: Path) -> None:
    repo = tmp_path
    skills = repo / ".cursor" / "skills" / "demo-skill"
    skills.mkdir(parents=True)
    skills.joinpath("SKILL.md").write_text("stub", encoding="utf-8")
    client = _mock_client({"demo-skill": _entity("demo-skill")})
    assert run_verify_manifest(client, repo) == 1
    payload = {
        "sot_snapshot_hash": compute_sot_snapshot_hash(
            [
                (
                    "demo-skill",
                    extract_renderer_fields(_entity("demo-skill"), "demo-skill"),
                )
            ]
        ),
        "generator_version": GENERATOR_VERSION,
        "allowlist_hash": "sha256:deadbeef",
        "output_tree_hash": compute_output_tree_hash(repo / ".cursor" / "skills"),
    }
    write_manifest(repo, payload)
    assert run_verify_manifest(client, repo) == 1


@pytest.mark.offline
def test_generator_version_mismatch_reports_dirty(tmp_path: Path) -> None:
    repo = tmp_path
    entity = _entity("version-skill")
    fields = extract_renderer_fields(entity, "version-skill")
    skills_dir = repo / ".cursor" / "skills" / "version-skill"
    skills_dir.mkdir(parents=True)
    skills_dir.joinpath("SKILL.md").write_text(render_stub("version-skill", fields))
    client = _mock_client({"version-skill": entity})
    from _skill_audit import _PARITY_ALLOWLIST
    from _skill_manifest import compute_allowlist_hash

    write_manifest(
        repo,
        {
            "sot_snapshot_hash": compute_sot_snapshot_hash([("version-skill", fields)]),
            "generator_version": "0.0.0-old",
            "allowlist_hash": compute_allowlist_hash(_PARITY_ALLOWLIST),
            "generated_count": 1,
            "skipped_allowlist": [],
            "output_tree_hash": compute_output_tree_hash(repo / ".cursor" / "skills"),
            "checked_at": "2026-01-01T00:00:00+00:00",
        },
    )
    status, problems = verify_manifest(repo, client)
    assert status == "dirty"
    assert any("generator_version" in line for line in problems)


@pytest.mark.offline
def test_sot_snapshot_hash_changes_when_skill_added(tmp_path: Path) -> None:
    one = [("alpha", extract_renderer_fields(_entity("alpha"), "alpha"))]
    two = one + [("beta", extract_renderer_fields(_entity("beta"), "beta"))]
    assert compute_sot_snapshot_hash(one) != compute_sot_snapshot_hash(two)


@pytest.mark.offline
def test_stub_critical_missing_field_blocks_generation(tmp_path: Path) -> None:
    bad = _entity("bad-skill", description="", terms=[])
    client = _mock_client({"bad-skill": bad})
    with patch("gen_skill_stubs._edge_drift_verdict", return_value=("clean", [])):
        with patch("gen_skill_stubs._scan_skills", return_value={}):
            with patch("gen_skill_stubs._scan_cortex_sot_declared", return_value={}):
                code = run_generate(client, tmp_path)
    assert code == 1


@pytest.mark.offline
def test_check_emits_five_verdicts(tmp_path: Path) -> None:
    client = _mock_client({})
    with patch("gen_skill_stubs._edge_drift_verdict", return_value=("clean", [])):
        with patch("gen_skill_stubs._sot_drift_verdict", return_value=("clean", [])):
            with patch("gen_skill_stubs.scanned_stub_slugs", return_value={}):
                with patch(
                    "gen_skill_stubs.stub_critical_field_verdict",
                    return_value=("clean", [], set()),
                ):
                    with patch(
                        "gen_skill_stubs.generator_manifest_verdict",
                        return_value=("error", ["missing manifest"]),
                    ):
                        with patch(
                            "gen_skill_stubs.allowlist_verdict",
                            return_value=("clean", []),
                        ):
                            code = run_check(client, tmp_path)
    assert code == 1


@pytest.mark.offline
def test_regenerate_stub_byte_identical(tmp_path: Path) -> None:
    slug = "regen-skill"
    entity = _entity(slug, related=["other-skill"])
    fields = extract_renderer_fields(entity, slug)
    content = render_stub(slug, fields)
    stub_path = tmp_path / ".cursor" / "skills" / slug / "SKILL.md"
    stub_path.parent.mkdir(parents=True)
    stub_path.write_text(content, encoding="utf-8")
    before = hashlib.sha256(stub_path.read_bytes()).hexdigest()
    stub_path.unlink()
    stub_path.write_text(render_stub(slug, extract_renderer_fields(entity, slug)))
    after = hashlib.sha256(stub_path.read_bytes()).hexdigest()
    assert before == after


@pytest.mark.offline
def test_allowlist_expired_entry_errors() -> None:
    from _skill_audit import _PARITY_ALLOWLIST, allowlist_verdict

    original = dict(_PARITY_ALLOWLIST)
    try:
        _PARITY_ALLOWLIST["expired-skill"] = {
            "reason": "test",
            "owner": "test",
            "expiry_or_assertion_ref": "2000-01-01",
            "directionality": "cortex-only",
            "temporary_or_structural": "temporary",
        }
        status, lines = allowlist_verdict()
        assert status == "error"
        assert lines
    finally:
        _PARITY_ALLOWLIST.clear()
        _PARITY_ALLOWLIST.update(original)


@pytest.mark.offline
def test_allowlist_missing_metadata_warns_dirty() -> None:
    from _skill_audit import _PARITY_ALLOWLIST, allowlist_verdict

    original = dict(_PARITY_ALLOWLIST)
    try:
        _PARITY_ALLOWLIST["incomplete-skill"] = {"reason": "only reason"}
        status, lines = allowlist_verdict()
        assert status == "dirty"
        assert any("missing metadata" in line for line in lines)
    finally:
        _PARITY_ALLOWLIST.clear()
        _PARITY_ALLOWLIST.update(original)


@pytest.mark.offline
def test_skill_graph_staleness_cue_uses_verify_manifest(tmp_path: Path) -> None:
    client = _mock_client({})
    cue = skill_graph_staleness_cue(tmp_path, client)
    assert cue is not None
    assert REMEDIATION_CMD in cue


@pytest.mark.offline
def test_first_run_bootstrap_writes_manifest_without_existing_manifest(
    tmp_path: Path,
) -> None:
    slug = "bootstrap-skill"
    entity = _entity(slug)
    fields = extract_renderer_fields(entity, slug)
    client = _mock_client({slug: entity})
    assert read_manifest(tmp_path) is None
    with patch("gen_skill_stubs._edge_drift_verdict", return_value=("clean", [])):
        with patch("gen_skill_stubs._sot_drift_verdict", return_value=("clean", [])):
            with patch("gen_skill_stubs._scan_cortex_sot_declared", return_value={}):
                with patch("gen_skill_stubs.parity_verdict", return_value=("clean", [])):
                    code = run_generate(client, tmp_path)
    assert code == 0
    manifest = read_manifest(tmp_path)
    assert manifest is not None
    assert manifest["generator_version"] == GENERATOR_VERSION
    assert manifest["generated_count"] == 1
    stub_path = tmp_path / ".cursor" / "skills" / slug / "SKILL.md"
    assert stub_path.is_file()
    assert stub_path.read_text(encoding="utf-8") == render_stub(slug, fields)
    status, problems = verify_manifest(tmp_path, client)
    assert status == "clean", problems


@pytest.mark.offline
def test_manifest_written_only_after_clean_generate(tmp_path: Path) -> None:
    slug = "manifest-skill"
    entity = _entity(slug)
    client = _mock_client({slug: entity})
    with patch("gen_skill_stubs.run_check", return_value=0):
        with patch(
            "gen_skill_stubs.build_manifest_payload",
            return_value={
                "sot_snapshot_hash": "sha256:abc",
                "generator_version": GENERATOR_VERSION,
            },
        ):
            assert run_generate(client, tmp_path) == 0
            assert read_manifest(tmp_path) is not None


@pytest.mark.offline
def test_reconcile_idempotent_skips_manifest_rewrite(tmp_path: Path) -> None:
    slug = "noop-skill"
    entity = _entity(slug)
    fields = extract_renderer_fields(entity, slug)
    content = render_stub(slug, fields)
    stub_dir = tmp_path / ".cursor" / "skills" / slug
    stub_dir.mkdir(parents=True)
    stub_dir.joinpath("SKILL.md").write_text(content, encoding="utf-8")
    payload = {
        "sot_snapshot_hash": "sha256:noop",
        "generator_version": GENERATOR_VERSION,
    }
    write_manifest(tmp_path, payload)
    before_mtime = manifest_path(tmp_path).stat().st_mtime
    client = _mock_client({slug: entity})
    with patch("gen_skill_stubs.run_check", return_value=0):
        with patch("gen_skill_stubs.build_manifest_payload", return_value=payload):
            assert run_generate(client, tmp_path) == 0
    assert manifest_path(tmp_path).stat().st_mtime == before_mtime


@pytest.mark.offline
def test_content_change_rewrites_stale_manifest(tmp_path: Path) -> None:
    slug = "change-skill"
    client = _mock_client({slug: _entity(slug)})
    with patch("gen_skill_stubs._edge_drift_verdict", return_value=("clean", [])):
        with patch("gen_skill_stubs._sot_drift_verdict", return_value=("clean", [])):
            with patch("gen_skill_stubs._scan_cortex_sot_declared", return_value={}):
                with patch("gen_skill_stubs.parity_verdict", return_value=("clean", [])):
                    assert run_generate(client, tmp_path) == 0
                    first = read_manifest(tmp_path)
                    assert first is not None
                    client2 = _mock_client(
                        {slug: _entity(slug, description="Changed description text.")}
                    )
                    assert run_generate(client2, tmp_path) == 0
    updated = read_manifest(tmp_path)
    assert updated is not None
    assert updated["sot_snapshot_hash"] != first["sot_snapshot_hash"]
    status, problems = verify_manifest(tmp_path, client2)
    assert status == "clean", problems


def _write_twin(repo: Path, slug: str) -> None:
    stub_dir = repo / ".cursor" / "skills" / slug
    stub_dir.mkdir(parents=True, exist_ok=True)
    stub_dir.joinpath("SKILL.md").write_text("stub", encoding="utf-8")


@pytest.mark.offline
def test_sot_drift_clean_when_cursor_sot_and_twin(tmp_path: Path) -> None:
    slug = "clean-skill"
    entity = _entity(
        slug,
        source_uri=f"workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md",
    )
    _write_twin(tmp_path, slug)
    client = _mock_client({slug: entity})
    status, lines = _sot_drift_verdict(client, tmp_path)
    assert status == "clean"
    assert lines == []


@pytest.mark.offline
def test_sot_drift_dirty_cortex_tier_source_uri(tmp_path: Path) -> None:
    slug = "cortex-source-skill"
    # Intentional dirty case: cortex-tier agent-skills/ URI (not workspaces SOT).
    entity = _entity(slug, source_uri="agent-skills/cortex-source-skill.md")
    _write_twin(tmp_path, slug)
    client = _mock_client({slug: entity})
    status, lines = _sot_drift_verdict(client, tmp_path)
    assert status == "dirty"
    assert len(lines) == 1
    assert "agent_skill:cortex-source-skill" in lines[0]
    assert "A cortex-tier source_uri" in lines[0]


@pytest.mark.offline
def test_sot_drift_dirty_missing_cursor_twin(tmp_path: Path) -> None:
    slug = "missing-twin-skill"
    entity = _entity(
        slug,
        source_uri=f"workspaces://universal-llm-gateway/.cursor/skills/{slug}/SKILL.md",
    )
    client = _mock_client({slug: entity})
    status, lines = _sot_drift_verdict(client, tmp_path)
    assert status == "dirty"
    assert len(lines) == 1
    assert "agent_skill:missing-twin-skill" in lines[0]
    assert "B missing .cursor twin" in lines[0]


@pytest.mark.offline
def test_sot_drift_holdout_not_flagged(tmp_path: Path) -> None:
    slug = "todo-lifecycle"
    entity = _entity(slug, source_uri="workspaces://universal-llm-gateway/.cursor/skills/todo-lifecycle/SKILL.md")
    client = _mock_client({slug: entity})
    status, lines = _sot_drift_verdict(client, tmp_path)
    assert status == "clean"
    assert lines == []


@pytest.mark.offline
def test_sot_drift_retired_suppressed(tmp_path: Path) -> None:
    slug = "retired-skill"
    entity = _entity(slug, source_uri="workspaces://universal-llm-gateway/.cursor/skills/retired-skill/SKILL.md")
    entity["lifecycle"] = "retired"
    client = _mock_client({slug: entity})
    status, lines = _sot_drift_verdict(client, tmp_path)
    assert status == "clean"
    assert lines == []


@pytest.mark.offline
def test_sot_drift_known_residual_not_dirty(tmp_path: Path) -> None:
    slug = "cortex-v24-implementation-arc"
    entity = _entity(slug, source_uri="workspaces://universal-llm-gateway/.cursor/skills/cortex-v24-implementation-arc/SKILL.md")
    client = _mock_client({slug: entity})
    status, lines = _sot_drift_verdict(client, tmp_path)
    assert status == "clean"
    assert len(lines) == 1
    assert lines[0].startswith("KNOWN-RESIDUAL:")
    assert "agent_skill:cortex-v24-implementation-arc" in lines[0]
