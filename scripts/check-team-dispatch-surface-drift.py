#!/usr/bin/env -S python3.12
"""AST drift gate: team_dispatch live params vs canonical.yaml json_schema.

Compares keyword parameter names from ``services/mcp-server/tools/frontier.py``
intersected with Pydantic fields from ``route.py`` against
``config/mcp/canonical.yaml`` for ``team_dispatch_generate`` and
``team_dispatch_to_thread``. Name parity only (v1).

Exit 0 if clean, 1 on drift. Run via scripts/agent-surface-check.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTIER_PY = REPO_ROOT / "services/mcp-server/tools/frontier.py"
ROUTE_PY = (
    REPO_ROOT / "services/universal-stargate/systems/frontier_consult/route.py"
)
DEFAULT_CANONICAL = REPO_ROOT / "config/mcp/canonical.yaml"

# v1 scope-down (spec F1): parity for Stage-1 surface knobs only — not the full
# HTTP/param surface (descriptor json_schema is intentionally partial).
KNOB_PARAMS = frozenset({"mcp", "skills", "server_tools"})

INTENTIONAL_OMISSIONS: dict[str, set[str]] = {
    "team_dispatch_generate": set(),
    "team_dispatch_to_thread": set(),
}

OP_MODELS: dict[str, tuple[str, str]] = {
    "team_dispatch_generate": ("_DispatchCommon", "TeamDispatchGenerateBody"),
    "team_dispatch_to_thread": ("_DispatchCommon", "TeamDispatchToThreadBody"),
}


def _find_function(tree: ast.AST, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _param_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    args = fn.args
    for arg in args.args + args.kwonlyargs:
        if arg.arg in {"self"} or arg.arg.startswith("_"):
            continue
        names.add(arg.arg)
    return names


def _class_field_names(tree: ast.AST, class_name: str) -> set[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        fields: set[str] = set()
        for item in node.body:
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                fields.add(item.target.id)
        return fields
    return set()


def _live_params_for_op(frontier_params: set[str], route_tree: ast.AST, op: str) -> set[str]:
    common_name, body_name = OP_MODELS[op]
    common = _class_field_names(route_tree, common_name)
    body = _class_field_names(route_tree, body_name)
    return (common | body) & frontier_params


def _load_schema_properties(canonical_path: Path) -> dict[str, set[str]]:
    try:
        from ruamel.yaml import YAML
    except ImportError as exc:
        raise RuntimeError("ruamel.yaml required") from exc

    yaml = YAML()
    data = yaml.load(canonical_path.read_text(encoding="utf-8"))
    by_name: dict[str, set[str]] = {}
    for tool in data.get("tools", []):
        name = tool.get("canonical_name")
        if name not in OP_MODELS:
            continue
        schema = tool.get("json_schema") or {}
        props = schema.get("properties") or {}
        by_name[name] = set(props.keys())
    return by_name


def check(canonical_path: Path) -> list[str]:
    frontier_tree = ast.parse(FRONTIER_PY.read_text(encoding="utf-8"))
    route_tree = ast.parse(ROUTE_PY.read_text(encoding="utf-8"))
    team_dispatch = _find_function(frontier_tree, "team_dispatch")
    if team_dispatch is None:
        return ["team_dispatch: function not found in frontier.py"]

    frontier_params = _param_names(team_dispatch)
    schema_by_op = _load_schema_properties(canonical_path)
    errors: list[str] = []

    for op in OP_MODELS:
        live = _live_params_for_op(frontier_params, route_tree, op) & KNOB_PARAMS
        schema = schema_by_op.get(op, set()) & KNOB_PARAMS
        allow = INTENTIONAL_OMISSIONS.get(op, set())
        missing = sorted(live - schema - allow)
        extra = sorted(schema - live)
        if missing or extra:
            errors.append(
                f"{op}: missing_in_schema={missing!r} extra_in_schema={extra!r}"
            )
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--canonical-yaml",
        type=Path,
        default=DEFAULT_CANONICAL,
        help="Path to canonical.yaml (default: repo config/mcp/canonical.yaml)",
    )
    args = parser.parse_args(argv)

    errors = check(args.canonical_yaml)
    if errors:
        for line in errors:
            print(line, file=sys.stderr)
        return 1

    print("OK check-team-dispatch-surface-drift")
    return 0


if __name__ == "__main__":
    sys.exit(main())
