"""Boot-time tool coherence guards (_derive inverse + forward)."""

from __future__ import annotations

import sys
from pathlib import Path

_MCP_SERVER = Path(__file__).resolve().parent.parent
_REPO_ROOT = _MCP_SERVER.parent.parent
sys.path.insert(0, str(_MCP_SERVER))

from _coherence_allowlist import INTENTIONAL_OVERFLOW  # noqa: E402
from _derive import (  # noqa: E402
    derive_all_canonical_tool_names,
    validate_registered_tool_coherence,
)

_CANONICAL = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"


def test_validate_registered_tool_coherence_flags_missing_git_land() -> None:
    violations = validate_registered_tool_coherence(
        {"git_land", "cortex"},
        allowlist=INTENTIONAL_OVERFLOW,
        canonical_yaml_path=_CANONICAL,
    )
    assert "cortex" not in violations
    assert "git_land" not in violations


def test_validate_registered_tool_coherence_fixture_missing_entry() -> None:
    canonical_names = derive_all_canonical_tool_names(_CANONICAL)
    assert "git_land" in canonical_names
    violations = validate_registered_tool_coherence(
        {"git_land", "totally_fake_tool_xyz"},
        allowlist=INTENTIONAL_OVERFLOW,
        canonical_yaml_path=_CANONICAL,
    )
    assert violations == ["totally_fake_tool_xyz"]


def test_ci_registered_tools_match_canonical_or_allowlist() -> None:
    from server import _build_server  # noqa: PLC0415

    _mcp, pre_prune, _overflow_md, _overflow_reg = _build_server()
    registered = set(pre_prune.keys())
    drift = validate_registered_tool_coherence(
        registered,
        allowlist=INTENTIONAL_OVERFLOW,
        canonical_yaml_path=_CANONICAL,
    )
    assert drift == [], (
        f"undeclared registered tools (add canonical or allowlist): {drift}"
    )
