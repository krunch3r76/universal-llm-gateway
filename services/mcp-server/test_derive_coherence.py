"""Tests for registered-tool coherence guard (Phase 2 / git-land-catalog-exposure).

Covers:
  - derive_all_canonical_tool_names: returns flat + dispatcher names from canonical.yaml
  - validate_registered_tool_coherence: inverse drift check (registered ⊄ canonical ⊄ allowlist)
  - CI invariant gate: current canonical.yaml contains git_land (post Phase 1)
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import yaml  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))
from _coherence_allowlist import INTENTIONAL_OVERFLOW
from _derive import (
    derive_all_canonical_tool_names,
    validate_primary_tools_coherence,
    validate_registered_tool_coherence,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_CANONICAL_YAML = _REPO_ROOT / "config" / "mcp" / "canonical.yaml"


# ── derive_all_canonical_tool_names ──────────────────────────────────────────


def test_derive_all_canonical_tool_names_nonempty() -> None:
    """Real canonical.yaml yields a non-empty name set."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    assert names, "Expected non-empty canonical name set"


def test_derive_all_canonical_tool_names_includes_known() -> None:
    """Known flat and dispatcher shapes are present."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    expected = {
        "cortex",
        "agent_bus",
        "fs",
        "dispatch",
        "git_land",
        "rag",
        "panel_dispatch",
    }
    missing = expected - names
    assert not missing, f"Expected names absent from canonical: {sorted(missing)}"


def test_derive_all_canonical_tool_names_fixture() -> None:
    """Returns flat + dispatcher shapes from a minimal fixture YAML."""
    fixture: dict = {
        "schema_version": 1,
        "tools": [
            {
                "canonical_name": "alpha_read",
                "domain": "alpha",
                "flat_call_shape": {"tool": "alpha_read"},
                "dispatcher_call_shape": {
                    "tool": "alpha",
                    "dispatch_key": "op",
                    "dispatch_value": "read",
                },
                "seat_visibility": ["mcp_claude"],
            },
            {
                "canonical_name": "beta_write",
                "domain": "beta",
                "flat_call_shape": {"tool": "beta_write"},
                "dispatcher_call_shape": {
                    "tool": "beta",
                    "dispatch_key": "op",
                    "dispatch_value": "write",
                },
                "seat_visibility": ["mcp", "mcp_claude"],
            },
        ],
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(fixture, f)
        tmp = Path(f.name)
    names = derive_all_canonical_tool_names(tmp)
    assert names == {"alpha_read", "alpha", "beta_write", "beta"}


# ── validate_registered_tool_coherence ───────────────────────────────────────


def test_coherence_detects_unregistered_tool() -> None:
    """git_land missing from fixture canonical → returned as violation."""
    fixture: dict = {
        "schema_version": 1,
        "tools": [
            {
                "canonical_name": "cortex_search",
                "domain": "cortex",
                "flat_call_shape": {"tool": "cortex_search"},
                "dispatcher_call_shape": {
                    "tool": "cortex",
                    "dispatch_key": "op",
                    "dispatch_value": "search",
                },
                "seat_visibility": ["mcp_claude"],
            },
        ],
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(fixture, f)
        tmp = Path(f.name)

    violations = validate_registered_tool_coherence(
        {"git_land", "cortex"},
        allowlist=frozenset(),
        canonical_yaml_path=tmp,
    )
    assert violations == ["git_land"]


def test_coherence_allowlist_suppresses_violation() -> None:
    """A tool in the allowlist is not reported as drift."""
    fixture: dict = {"schema_version": 1, "tools": []}
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(fixture, f)
        tmp = Path(f.name)

    violations = validate_registered_tool_coherence(
        {"health", "cortex"},
        allowlist=frozenset({"health"}),
        canonical_yaml_path=tmp,
    )
    assert violations == ["cortex"]


def test_coherence_clean_when_all_canonical() -> None:
    """No violations when all registered tools are in canonical."""
    fixture: dict = {
        "schema_version": 1,
        "tools": [
            {
                "canonical_name": "cortex_search",
                "domain": "cortex",
                "flat_call_shape": {"tool": "cortex_search"},
                "dispatcher_call_shape": {
                    "tool": "cortex",
                    "dispatch_key": "op",
                    "dispatch_value": "search",
                },
                "seat_visibility": ["mcp_claude"],
            },
        ],
    }
    with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as f:
        yaml.dump(fixture, f)
        tmp = Path(f.name)

    violations = validate_registered_tool_coherence(
        {"cortex_search", "cortex"},
        allowlist=frozenset(),
        canonical_yaml_path=tmp,
    )
    assert violations == []


# ── Post-Phase-1 invariant: git_land declared in canonical ────────────────────


def test_git_land_in_canonical_post_phase1() -> None:
    """git_land must be in canonical.yaml after Phase 1 (not in overflow)."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    assert "git_land" in names, (
        "git_land absent from canonical.yaml — Phase 1 prerequisite not met. "
        "Add git_land entry to config/mcp/canonical.yaml."
    )


def test_panel_dispatch_in_canonical_post_phase2() -> None:
    """panel_dispatch must be in canonical.yaml (thread 1206 Phase 2)."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    assert "panel_dispatch" in names, (
        "panel_dispatch absent from canonical.yaml — add entry before mcp-server rebuild."
    )


def test_imprint_in_canonical_life_mcp() -> None:
    """imprint domain must be canonicalized for life-only MCP surface (sw-imprint AC7)."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    assert "imprint" in names, (
        "imprint absent from canonical.yaml — add imprint_propose entry."
    )
    assert "imprint" not in INTENTIONAL_OVERFLOW, (
        "imprint must not be in INTENTIONAL_OVERFLOW once canonicalized."
    )


def test_delegate_in_canonical_life_mcp() -> None:
    """delegate domain must be canonicalized for life-only MCP surface (AC8)."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    assert "delegate" in names, (
        "delegate absent from canonical.yaml — add delegate_propose entry."
    )
    assert "delegate" not in INTENTIONAL_OVERFLOW, (
        "delegate must not be in INTENTIONAL_OVERFLOW once canonicalized."
    )


def test_notify_in_canonical_life_mcp() -> None:
    """notify domain must be canonicalized for life-only MCP surface (inform-Kaywan v1)."""
    names = derive_all_canonical_tool_names(_CANONICAL_YAML)
    assert "notify" in names, (
        "notify absent from canonical.yaml — add notify_send entry."
    )
    assert "notify" not in INTENTIONAL_OVERFLOW, (
        "notify must not be in INTENTIONAL_OVERFLOW once canonicalized."
    )


def test_git_land_not_in_intentional_overflow() -> None:
    """git_land must not be in INTENTIONAL_OVERFLOW once canonicalized."""
    assert "git_land" not in INTENTIONAL_OVERFLOW, (
        "git_land should be removed from INTENTIONAL_OVERFLOW now that it is in canonical.yaml"
    )


# ── CI invariant gate ─────────────────────────────────────────────────────────


def _collect_registered_tool_names() -> set[str]:
    """Enumerate tool names registered by the standard (non-local, non-browser) tool modules.

    Loads the production server module to drive registration against a mock FastMCP,
    capturing names from the standard tool modules only. Local tools (tools.local/)
    are excluded — they are gitignored and legitimately absent in CI.

    Returns the pre-prune registered name set for coherence verification.
    """
    from unittest.mock import MagicMock

    # Collect tool names as they are registered via @mcp.tool decorators.
    registered: set[str] = set()

    class _CaptureMCP:
        """Minimal FastMCP stand-in that captures @mcp.tool registrations."""

        def tool(self, *args: object, **kwargs: object):  # noqa: ANN201
            def decorator(fn: object) -> object:
                name = kwargs.get("name") or (fn.__name__ if callable(fn) else str(fn))
                registered.add(name)
                return fn

            # Called as @mcp.tool() (with parens) or @mcp.tool(title=...) etc.
            if args and callable(args[0]) and not kwargs:
                fn = args[0]
                registered.add(fn.__name__)
                return fn
            return decorator

        def __getattr__(self, item: str) -> MagicMock:
            return MagicMock()

    mcp = _CaptureMCP()

    # Import and call each standard register function. We import lazily to avoid
    # side effects from the full server module (OAuth, uvicorn, etc.).
    tool_modules = [
        ("tools.filesystem", "register_filesystem_tools"),
        ("tools.markdown_tool", "register_markdown_tools"),
        ("tools.manage", "register_manage_tools"),
        ("tools.model_status", "register_model_status_tools"),
        ("tools.topology", "register_topology_tools"),
        ("tools.project", "register_project_tools"),
        ("tools.web", "register_web_tools"),
        ("tools.browse", "register_browse_tool"),
        ("tools.rag", "register_rag_tools"),
        ("tools.rag_articles", "register_rag_article_tools"),
        ("tools.context", "register_context_tools"),
        ("tools.sqlite", "register_sqlite_tools"),
        ("tools.events", "register_event_tools"),
        ("tools.extract_document", "register_extract_document_tools"),
        (
            "tools.promote_document_to_evidence",
            "register_promote_document_to_evidence_tools",
        ),
        ("tools.extract_directory", "register_extract_directory_tools"),
        ("tools.pipeline", "register_pipeline_tools"),
        ("tools.pipeline_consult", "register_pipeline_consult_tools"),
        ("tools.frontier", "register_frontier_tools"),
        ("tools.panel_dispatch", "register_panel_dispatch_tools"),
        ("tools.git_integrate", "register_git_integrate_tools"),
        ("tools.imprint", "register_imprint_tools"),
        ("tools.delegate", "register_delegate_tools"),
        ("tools.notify", "register_notify_tools"),
        ("tools.quality", "register_quality_tools"),
        ("tools.agent_bus", "register_agent_bus_tools"),
        ("tools.cortex", "register_cortex_tools"),
        ("tools.cortex_named_tools", "register_cortex_named_tools"),
        ("tools.advisor", "register_advisor_tools"),
        ("tools.frontier_imagine", "register_imagine_tools"),
        ("tools.security", "register_security_tools"),
        ("tools.security_js", "register_security_js_tools"),
    ]

    for mod_name, fn_name in tool_modules:
        try:
            import importlib

            mod = importlib.import_module(mod_name)
            fn = getattr(mod, fn_name, None)
            if fn is not None:
                fn(mcp)
        except Exception:  # noqa: BLE001
            pass  # skip modules with unresolvable dependencies in test context

    # Inline tool registered directly in _build_server (health check)
    registered.add("health")

    return registered


def test_ci_invariant_no_coherence_drift() -> None:
    """CI gate: all registered tools are in canonical.yaml or INTENTIONAL_OVERFLOW.

    ∀ t ∈ registered: t ∈ canonical_names ∨ t ∈ INTENTIONAL_OVERFLOW ∨ FAIL.

    This test enforces the invariant that prevents git_land-class drift: adding a
    new @mcp.tool without a canonical.yaml entry silently demotes it to overflow.
    A PR that triggers this test must either add a canonical.yaml entry (preferred)
    or add the tool name to INTENTIONAL_OVERFLOW with a "why" comment.
    """
    registered = _collect_registered_tool_names()
    violations = validate_registered_tool_coherence(
        registered,
        allowlist=INTENTIONAL_OVERFLOW,
        canonical_yaml_path=_CANONICAL_YAML,
    )
    assert violations == [], (
        f"Tool coherence drift — registered but undeclared in canonical.yaml "
        f"and not in INTENTIONAL_OVERFLOW:\n  {violations}\n\n"
        "To fix: either add a canonical.yaml entry (promotes the tool to first-class) "
        "or add the tool name to INTENTIONAL_OVERFLOW in _coherence_allowlist.py "
        "with a comment explaining why it is legitimately overflow."
    )


def test_ci_forward_coherence() -> None:
    """CI gate: all primary tools have canonical domains (forward direction).

    Ensures _PRIMARY_TOOLS (claude manifest dispatcher names) are all in canonical.
    """
    # Derive primary tools the same way server.py does.
    from _derive import derive_claude_manifest

    claude_manifest = derive_claude_manifest(_CANONICAL_YAML)
    primary_tools = {e["tool_name"] for e in claude_manifest}
    violations = validate_primary_tools_coherence(primary_tools, _CANONICAL_YAML)
    assert violations == [], (
        f"Primary tools absent from canonical registry: {violations}"
    )
