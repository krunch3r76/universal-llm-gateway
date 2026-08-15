"""Seat-facing INJECTORS nomination — distinct from CONSUMERS import-nomination."""

from __future__ import annotations

import pytest

from implement_admission.consumer_import_verify import (
    clear_verify_caches,
    parse_verification_tags,
    repo_root,
    verify_consumer_import,
)
from implement_admission.injector_map import (
    check_injectors_declarations,
    check_nomination_declarations,
    injectors_declared_in_source,
    iter_injectors_declarations,
    nominations_for_lib_path,
    tuple_declared_in_source,
)

_OPERATOR_PROXY_BRIEFINGS = (
    "libs/claude_bundles/operator_proxy_tier_m.py",
    "libs/claude_bundles/operator_proxy_mission.py",
    "libs/claude_bundles/operator_proxy_wake_brief.py",
)


def test_injectors_declared_in_source_parses_annotated_tuple():
    text = 'INJECTORS: tuple[str, ...] = ("cdp_ask",)\n'
    assert injectors_declared_in_source(text) == ("cdp_ask",)
    assert injectors_declared_in_source("X = 1\n") is None
    assert injectors_declared_in_source('CONSUMERS = ("mcp",)\n') is None


def test_parse_verification_tags_accepts_injectors_derived():
    tagged = "derived:injectors; import_path:verified"
    assert parse_verification_tags(tagged) == {
        "derived": "injectors",
        "import_path": "verified",
    }


def test_parse_verification_tags_accepts_serves_derived():
    tagged = "derived:serves; import_path:verified"
    assert parse_verification_tags(tagged) == {
        "derived": "serves",
        "import_path": "verified",
    }


@pytest.mark.offline
def test_operator_proxy_briefings_declare_cdp_ask_injectors():
    """Briefing paste path is cdp_ask, not the GIW importer."""
    clear_verify_caches()
    decls = dict(iter_injectors_declarations())
    for path in _OPERATOR_PROXY_BRIEFINGS:
        assert decls[path] == ("cdp_ask",), path
        assert verify_consumer_import("cdp_ask", path) == "verified"


@pytest.mark.offline
def test_tier_m_nominations_are_injector_then_consumer():
    clear_verify_caches()
    path = "libs/claude_bundles/operator_proxy_tier_m.py"
    assert nominations_for_lib_path(path) == (
        ("cdp_ask", "injectors"),
        ("git_integration_worker", "consumers"),
    )


@pytest.mark.offline
def test_chat_cowork_mode_has_no_injectors_tuple():
    """Compose helper already names cdp_ask in CONSUMERS; no parallel INJECTORS."""
    clear_verify_caches()
    decls = dict(iter_injectors_declarations())
    assert "libs/claude_bundles/chat_cowork_mode.py" not in decls
    assert nominations_for_lib_path("libs/claude_bundles/chat_cowork_mode.py") == (
        ("cdp_ask", "consumers"),
    )


@pytest.mark.offline
def test_check_injectors_declarations_tree_is_clean():
    clear_verify_caches()
    failures = check_nomination_declarations()
    assert failures == [], "\n".join(failures)
    assert check_injectors_declarations() == []


@pytest.mark.offline
def test_wait_status_nominations_are_serving_agent_bus():
    """Replay 33083d61: harvest nominates the wait server, not owned_libs extras."""
    clear_verify_caches()
    path = "libs/agent_bus_store/wait_status.py"
    assert nominations_for_lib_path(path) == (("agent_bus", "serves"),)


@pytest.mark.offline
def test_boot_lane_readoption_nominations_match_disk_ast():
    """Residue reads on-disk CONSUMERS/INJECTORS, not a cached importlib view."""
    clear_verify_caches()
    path = "libs/claude_bundles/boot_lane_readoption.py"
    text = (repo_root() / path).read_text(encoding="utf-8")
    assert tuple_declared_in_source(text, "CONSUMERS") == ("cdp_ask",)
    assert tuple_declared_in_source(text, "INJECTORS") == ("cdp_ask",)
    assert nominations_for_lib_path(path) == (("cdp_ask", "injectors"),)
