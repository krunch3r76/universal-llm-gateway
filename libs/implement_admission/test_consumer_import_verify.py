"""Module-granular consumer import verification (oracle + negative control)."""

from __future__ import annotations

import pytest

from implement_admission.consumer_import_verify import (
    check_consumers_declarations,
    clear_verify_caches,
    consumers_declared_in_source,
    format_verification_tags,
    iter_consumers_declarations,
    module_for_lib_path,
    parse_verification_tags,
    verification_tags_fragment,
    verify_consumer_import,
)

_OPERATOR_PROXY_BRIEFINGS = (
    "libs/claude_bundles/operator_proxy_tier_m.py",
    "libs/claude_bundles/operator_proxy_mission.py",
    "libs/claude_bundles/operator_proxy_wake_brief.py",
)


def test_module_for_lib_path_file_and_init():
    assert (
        module_for_lib_path("libs/deploy_identity/code_ref_relation.py")
        == "deploy_identity.code_ref_relation"
    )
    assert module_for_lib_path("libs/deploy_identity/__init__.py") == "deploy_identity"
    assert module_for_lib_path("libs/foo.py") == "foo"
    assert module_for_lib_path("services/mcp-server/x.py") is None


def test_oracle_mcp_contradicted_for_code_ref_relation_sibling():
    """Package CONSUMERS nominates mcp; mcp imports code_version only."""
    path = "libs/deploy_identity/code_ref_relation.py"
    assert verify_consumer_import("mcp", path) == "contradicted"
    assert verify_consumer_import("git_integration_worker", path) == "verified"


def test_code_version_land_verifies_mcp_and_giw():
    path = "libs/deploy_identity/code_version.py"
    assert verify_consumer_import("mcp", path) == "verified"
    assert verify_consumer_import("git_integration_worker", path) == "verified"


def test_package_init_land_verifies_importers_of_submodules():
    path = "libs/deploy_identity/__init__.py"
    assert verify_consumer_import("mcp", path) == "verified"
    assert verify_consumer_import("git_integration_worker", path) == "verified"


def test_tags_are_machine_readable_kv():
    assert (
        format_verification_tags(derived="consumers", import_path="contradicted")
        == "derived:consumers; import_path:contradicted"
    )


def test_parse_verification_tags_round_trip_and_absence():
    tagged = format_verification_tags(
        derived="path_prefix", import_path="not_probed"
    )
    reason = f"path-derived obligation; liveness: unknown; {tagged}"
    assert parse_verification_tags(reason) == {
        "derived": "path_prefix",
        "import_path": "not_probed",
    }
    assert verification_tags_fragment(reason) == tagged
    # Pre-change generator default → not_probed at read (Fork 3).
    legacy = "derived:path_prefix; import_path:verified"
    assert parse_verification_tags(legacy) == {
        "derived": "path_prefix",
        "import_path": "not_probed",
    }
    assert parse_verification_tags("operator restart request via cursor-auto") is None
    assert verification_tags_fragment("operator restart request via cursor-auto") is None


def test_measure_blinds_and_mixed_residue_oracle():
    """Fork 1: mcp contradicted + blinds escalate; GIW verified still mints."""
    from implement_admission.consumer_import_blinds import measure_import_grammar_blinds
    from implement_admission.consumer_import_verify import (
        residue_actions_for_lib_consumers,
    )

    clear_verify_caches()
    path = "libs/deploy_identity/code_ref_relation.py"
    mcp_blinds = measure_import_grammar_blinds("mcp")
    assert mcp_blinds  # contact: service_relative/dynamic/from_import_name
    cortex_blinds = measure_import_grammar_blinds("cortex_api")
    assert cortex_blinds == frozenset()
    actions = residue_actions_for_lib_consumers(
        path, ("git_integration_worker", "mcp")
    )
    text = "\n".join(actions)
    assert "sync_restart: git_integration_worker" in text
    assert "sync_restart: mcp" not in text
    assert "libs_touched:" in text and "mcp" in text
    assert "import_grammar_blind:" in text
    # Earned omit: contradicted + zero blinds → no escalate for that slug alone.
    omit_only = residue_actions_for_lib_consumers(path, ("cortex_api",))
    assert omit_only == ()


def test_consumers_declared_in_source_parses_annotated_tuple():
    text = 'CONSUMERS: tuple[str, ...] = ("git_integration_worker", "mcp")\n'
    assert consumers_declared_in_source(text) == (
        "git_integration_worker",
        "mcp",
    )
    assert consumers_declared_in_source("X = 1\n") is None


@pytest.mark.offline
def test_check_consumers_declarations_tree_is_clean():
    """Authorship-time gate: every declared CONSUMERS slug must reach its file.

    Wired as ``@pytest.mark.offline`` so Lane-A CI
    (``pytest -m offline libs``) runs it — a check nothing executes is worse
    than none. Scans real ``libs/**`` declarations, not a synthetic fixture.
    """
    clear_verify_caches()
    failures = check_consumers_declarations()
    assert failures == [], "\n".join(failures)


@pytest.mark.offline
def test_deploy_identity_package_init_remains_verified_negative_control():
    """Package-grain CONSUMERS on deploy_identity/__init__ must stay verified."""
    clear_verify_caches()
    path = "libs/deploy_identity/__init__.py"
    decls = dict(iter_consumers_declarations())
    assert path in decls
    assert decls[path] == ("git_integration_worker", "mcp")
    assert verify_consumer_import("mcp", path) == "verified"
    assert verify_consumer_import("git_integration_worker", path) == "verified"
    assert not any(f.startswith(path + ":") for f in check_consumers_declarations())


@pytest.mark.offline
def test_operator_proxy_briefings_declare_giw_not_mcp():
    """Briefing modules declare GIW-only CONSUMERS (corrected authorship shape)."""
    clear_verify_caches()
    decls = dict(iter_consumers_declarations())
    for path in _OPERATOR_PROXY_BRIEFINGS:
        assert decls[path] == ("git_integration_worker",), path
        assert verify_consumer_import("git_integration_worker", path) == "verified"
