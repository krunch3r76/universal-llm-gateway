"""Hermetic check: advisor pipeline_options match chat-dispatch admission."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.advisor import (  # noqa: E402
    _ADVISOR_TEMPERATURE,
    _MAX_ADVICE_TOKENS,
    build_advisor_pipeline_options,
)

_HANDLER_PATH = (
    Path(__file__).resolve().parents[2]
    / "universal-stargate"
    / "systems"
    / "pipeline"
    / "core"
    / "handlers"
    / "frontier_dispatch"
    / "handler.py"
)


def _accepted_runtime_option_keys() -> frozenset[str]:
    """Parse ``FrontierDispatchHandler._ACCEPTED_RUNTIME_OPTION_KEYS`` from source.

    Avoids importing the pipeline package (heavy deps / ``libs.*`` path quirks)
    while still binding the test to the live admission allowlist.
    """
    tree = ast.parse(_HANDLER_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "FrontierDispatchHandler":
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.AnnAssign):
                continue
            target = stmt.target
            if not isinstance(target, ast.Name):
                continue
            if target.id != "_ACCEPTED_RUNTIME_OPTION_KEYS":
                continue
            if stmt.value is None:
                break
            # Annotated assign: frozenset({...}) — eval the set literal arg.
            call = stmt.value
            if not isinstance(call, ast.Call) or not call.args:
                break
            keys = ast.literal_eval(call.args[0])
            return frozenset(keys)
    raise AssertionError(
        f"could not parse _ACCEPTED_RUNTIME_OPTION_KEYS from {_HANDLER_PATH}"
    )


def test_advisor_options_accepted_by_frontier_dispatch_allowlist() -> None:
    options = build_advisor_pipeline_options("anthropic/claude-opus-4-6", 512)
    accepted = _accepted_runtime_option_keys()
    unknown = sorted(set(options) - accepted)
    assert unknown == [], f"unknown pipeline_options keys: {unknown}"

    assert options["mcp"] is False
    gen = options["generation_parameters"]
    assert gen["max_tokens"] == 512
    assert gen["temperature"] == _ADVISOR_TEMPERATURE
    assert "max_tokens" not in options
    assert "temperature" not in options


def test_advisor_options_default_max_tokens() -> None:
    options = build_advisor_pipeline_options("anthropic/claude-opus-4-6")
    assert options["generation_parameters"]["max_tokens"] == _MAX_ADVICE_TOKENS
