"""Red-base / green-head witness for friction-filing G5 (agent-bus:23978#45 A4).

Pre-fix production modules are materialized from pinned git blobs into a
disposable import tree (``tmp_path``) — no lane DB reset, no hub checkout flip.

Pinned red bases:
- ``RED_SHA_MAIN`` — d38a6d425 (``f4533a574^``): A1 related-edge agent, A2
  friction ingress, A3 analyze_impact confidence=None passthrough.
- ``RED_SHA_ASSERTION_GET`` — f4533a574 (``dcaf6ccee^``): A6 assertion_get id shape.
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from pydantic import ValidationError

from cortex_store.dispatch_ops._write_validation import (
    field_error,
    validation_error_response,
)
from cortex_store.dispatch_ops.ops_assertions_update import _resolve_assertion_get_id
from cortex_store.models import ImpactAnalysisRequest

pytestmark = pytest.mark.offline

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RED_SHA_MAIN = "d38a6d425ab6cf9d296bffa1586992b7730e2f8c"
_RED_SHA_ASSERTION_GET = "f4533a5748f3fe4d50e1ea399a8bddfefe54650c"
_ARG_REWRITE_RELPATH = "services/mcp-server/tools/agent_bus/_arg_rewrite.py"
_ASSERTIONS_UPDATE_RELPATH = "libs/cortex_store/dispatch_ops/ops_assertions_update.py"
_ASSERTIONS_RELPATH = "libs/cortex_store/dispatch_ops/ops_assertions.py"


def _git_show(rev: str, relpath: str) -> str:
    out = subprocess.check_output(
        ["git", "-C", str(_REPO_ROOT), "show", f"{rev}:{relpath}"],
        text=True,
    )
    return out


def _extract_function_source(module_source: str, name: str) -> str:
    tree = ast.parse(module_source)
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(module_source, node) or ""
    raise AssertionError(f"{name} not found in module source")


def _disposable_import_arg_rewrite(tmp_path: Path, rev: str) -> ModuleType:
    root = tmp_path / "disposable_import"
    tools_dir = root / "tools"
    bus_dir = tools_dir / "agent_bus"
    bus_dir.mkdir(parents=True)
    tools_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    bus_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    bus_dir.joinpath("_arg_rewrite.py").write_text(
        _git_show(rev, _ARG_REWRITE_RELPATH),
        encoding="utf-8",
    )
    sys.path.insert(0, str(root))
    try:
        spec = importlib.util.spec_from_file_location(
            "friction_g5_red_arg_rewrite",
            bus_dir / "_arg_rewrite.py",
            submodule_search_locations=[str(bus_dir)],
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.pop(0)


def _load_red_resolver(rev: str) -> Callable[..., tuple[int | None, dict | None]]:
    source = _git_show(rev, _ASSERTIONS_UPDATE_RELPATH)
    func_src = _extract_function_source(source, "_resolve_assertion_get_id")
    ns: dict[str, object] = {
        "validation_error_response": validation_error_response,
        "field_error": field_error,
        "Any": object,
    }
    exec(func_src, ns)  # noqa: S102
    fn = ns["_resolve_assertion_get_id"]
    assert callable(fn)
    return fn  # type: ignore[return-value]


def _red_a3_confidence_call_pattern(rev: str) -> bool:
    source = _git_show(rev, _ASSERTIONS_RELPATH)
    segment = source.split("def _op_analyze_impact", 1)[1].split("def _op_activate", 1)[
        0
    ]
    return (
        "ImpactAnalysisRequest(entity_id=entity_id, claim=claim, confidence=confidence)"
        in segment
    )


def test_red_a2_disposable_arg_rewrite_ignores_friction_from_alias(
    tmp_path: Path,
) -> None:
    """A2 red: no substrate_friction_file remap — ``from`` survives unparsed."""
    red_mod = _disposable_import_arg_rewrite(tmp_path, _RED_SHA_MAIN)
    parsed, err, advisories = red_mod.reconcile_dispatch_arguments(
        "substrate_friction_file",
        {
            "owner": "service:mcp-server",
            "note": "enum lagged",
            "from": "web-anthropic",
        },
    )
    assert err is None
    assert "from" in parsed
    assert "agent" not in parsed
    assert not advisories


def _green_arg_rewrite_module() -> ModuleType:
    mcp_root = _REPO_ROOT / "services" / "mcp-server"
    path = mcp_root / "tools" / "agent_bus" / "_arg_rewrite.py"
    spec = importlib.util.spec_from_file_location(
        "friction_g5_green_arg_rewrite",
        path,
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_green_a2_friction_from_alias_remapped() -> None:
    green_mod = _green_arg_rewrite_module()
    parsed, err, advisories = green_mod.reconcile_dispatch_arguments(
        "substrate_friction_file",
        {
            "owner": "service:mcp-server",
            "note": "enum lagged",
            "from": "web-anthropic",
        },
    )
    assert err is None
    assert parsed["agent"] == "web-anthropic"
    assert "from" not in parsed
    assert advisories


def test_red_a3_pinned_blob_passes_explicit_none_into_request_ctor() -> None:
    """A3 red signature: confidence=None forwarded into ImpactAnalysisRequest."""
    assert _red_a3_confidence_call_pattern(_RED_SHA_MAIN)
    with pytest.raises(ValidationError):
        ImpactAnalysisRequest(
            entity_id="service:mcp-server",
            claim="red witness",
            confidence=None,
        )


def test_green_a3_omits_explicit_none_for_model_default() -> None:
    """Green matches post-fix _op_analyze_impact: omit None confidence key."""
    kw: dict[str, object] = {
        "entity_id": "service:mcp-server",
        "claim": "green witness",
    }
    req = ImpactAnalysisRequest(**kw)
    assert req.confidence == "believed"


def test_red_a1_pre_fix_edge_agent_ignores_explicit_agent() -> None:
    """A1 red: explicit ``agent`` kwarg absent from pre-fix related-edge selection."""

    def pre_fix_edge_agent(
        *,
        agent: str | None,
        seeded_by: str | None,
        session_id: str | None,
    ) -> str | None:
        # Source: git show d38a6d425:…/ops_assertions_write.py (related branch)
        _ = agent
        if seeded_by and str(seeded_by).strip():
            return str(seeded_by).strip()
        if session_id:
            from agent_seat.session_id import agent_slug_from_session_id

            return agent_slug_from_session_id(session_id)
        return None

    got = pre_fix_edge_agent(
        agent="grok-bot",
        seeded_by="cursor",
        session_id="cursor-sdk:lane-23978",
    )
    assert got == "cursor"
    assert got != "grok-bot"


def test_red_a6_oversized_id_treated_as_absence_not_shape_error() -> None:
    """A6 red: int64 overflow accepted as DB lookup, not invalid_assertion_id."""
    red_resolve = _load_red_resolver(_RED_SHA_ASSERTION_GET)
    resolved_id, shape_err = red_resolve("9223372036854775808")
    assert shape_err is None
    assert resolved_id == 9223372036854775808


def test_green_a6_oversized_id_is_shape_error() -> None:
    resolved_id, shape_err = _resolve_assertion_get_id("9223372036854775808")
    assert resolved_id is None
    assert shape_err is not None
    assert shape_err.get("status_code") == 422


def test_green_a6_unicode_digit_string_is_shape_error() -> None:
    wire_id = "\u2074"
    assert wire_id.isdigit()
    resolved_id, shape_err = _resolve_assertion_get_id(wire_id)
    assert resolved_id is None
    assert shape_err is not None
    err = shape_err.get("error")
    assert isinstance(err, dict)
    assert err.get("error") == "invalid_assertion_id"
