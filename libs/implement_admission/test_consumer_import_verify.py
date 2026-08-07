"""Module-granular consumer import verification (oracle + negative control)."""

from __future__ import annotations

from implement_admission.consumer_import_verify import (
    format_verification_tags,
    module_for_lib_path,
    verify_consumer_import,
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
