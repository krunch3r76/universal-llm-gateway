"""CONSUMERS harvest nomination for host_predicates."""

from __future__ import annotations

import pytest
from implement_admission.consumer_import_verify import (
    clear_verify_caches,
    verify_consumer_import,
)
from implement_admission.injector_map import nominations_for_lib_path

pytestmark = pytest.mark.offline

_EXPECTED = frozenset(
    {"agent_bus", "cdp_ask", "git_integration_worker", "mcp", "stargate"}
)
_PATH = "libs/host_predicates/cursor_hosts.py"


def test_host_predicates_cursor_hosts_nominations_cover_importers() -> None:
    slugs = {slug for slug, _ in nominations_for_lib_path(_PATH)}
    assert _EXPECTED <= slugs


def test_host_predicates_consumers_import_verified() -> None:
    clear_verify_caches()
    for slug in sorted(_EXPECTED):
        assert verify_consumer_import(slug, _PATH) == "verified", slug
