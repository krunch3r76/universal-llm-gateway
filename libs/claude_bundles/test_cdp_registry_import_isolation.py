"""Cheap cdp_registry.models import must not load drain / idle-probe stacks."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.offline

_LIBS_ROOT = Path(__file__).resolve().parents[1]

_ISOLATION_SCRIPT = """
import sys
from claude_bundles.cdp_registry.models import _LISTABLE_STATUSES

assert "claude_bundles.cdp_registry.dormant_drain" not in sys.modules
assert "claude_bundles.cse_idle_probe" not in sys.modules
assert _LISTABLE_STATUSES == frozenset({"active", "orphaned_alive", "retained"})
"""


def test_cheap_models_import_does_not_load_drain_or_idle_probe() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_LIBS_ROOT)
    result = subprocess.run(
        [sys.executable, "-c", _ISOLATION_SCRIPT],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_reg_cdp_lane_resolves_lazily() -> None:
    import claude_bundles.cdp_registry as reg

    assert reg.cdp_lane is not None
