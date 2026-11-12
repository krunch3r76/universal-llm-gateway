"""C3: Stargate spawn/ledger code paths must not import ledger from cursor_bridge."""

from __future__ import annotations

import re

import pytest
from charter_runner_store.archive_pkg import import_archived

repo_root = import_archived("propagation_libs_closure").repo_root

_STARGATE_LEDGER_PATHS = (
    "services/universal-stargate/systems/frontier_consult/route_team_dispatch.py",
    "services/universal-stargate/systems/frontier_consult/generate_wrap.py",
    "services/universal-stargate/systems/frontier_consult/cdp_generate.py",
    "pipelines/team_dispatch_admit/v1/handlers/assemble.py",
)

_CURSOR_BRIDGE_LEDGER = re.compile(
    r"\bcursor_bridge\.spawn_endpoint_house_ledger\b|\bfrom cursor_bridge import .*spawn"
)


@pytest.mark.offline
@pytest.mark.parametrize("rel_path", _STARGATE_LEDGER_PATHS)
def test_stargate_spawn_ledger_paths_avoid_cursor_bridge_ledger(rel_path: str) -> None:
    text = (repo_root() / rel_path).read_text(encoding="utf-8")
    assert _CURSOR_BRIDGE_LEDGER.search(text) is None, rel_path
    assert "spawn_ledger" in text, rel_path
