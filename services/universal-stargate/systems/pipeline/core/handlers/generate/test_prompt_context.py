"""``render_user_prompt`` optional-placeholder validation tests.

Covers friction a23318: the doc_generate draft prompt declares
``existing_doc`` may be empty (first run — no architecture doc yet), but the
shared validator rejected any empty/whitespace-only placeholder value.

Contract under test (``PromptConfig.optional_placeholders``):
- Default remains strict — an empty value for an undeclared placeholder
  raises ``ValueError``.
- A declared-optional placeholder may resolve to ``""``/whitespace and the
  prompt renders with an empty substitution.
- Optionality does NOT waive resolvability — a declared-optional placeholder
  missing from the context still raises (unfilled placeholder).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from systems.pipeline.core.handlers.generate.prompt_context import render_user_prompt
from systems.pipeline.core.pipeline_config import PromptConfig
from systems.pipeline.core.prompts import PromptBuilder

_TEMPLATE = (
    "## Subsystem path\n{subsystem_path}\n\n"
    "## Existing document content (may be empty)\n{existing_doc}\n\n"
    "## Extracted inventory JSON\n{inventory_json}\n"
)

_STEP = SimpleNamespace(id="draft")
_CONTEXT = SimpleNamespace()  # opaque; only threaded through build_context


def _render(prompt_config: PromptConfig, ctx_values: dict[str, Any]) -> str:
    return render_user_prompt(
        prompt_config,
        _STEP,  # type: ignore[arg-type]
        _CONTEXT,  # type: ignore[arg-type]
        prompt_builder=PromptBuilder(),
        build_context=lambda _step, _context: ctx_values,
    )


def _prompt_config(optional_placeholders: list[str] | None = None) -> PromptConfig:
    kwargs: dict[str, Any] = {"name": "draft", "template": _TEMPLATE}
    if optional_placeholders is not None:
        kwargs["optional_placeholders"] = optional_placeholders
    return PromptConfig(**kwargs)


def test_strict_default_rejects_empty_placeholder() -> None:
    """Undeclared placeholders keep the strict non-empty check."""
    with pytest.raises(ValueError, match="'existing_doc' is empty or None"):
        _render(
            _prompt_config(),
            {
                "subsystem_path": "systems/pipeline",
                "existing_doc": "",
                "inventory_json": '{"modules": []}',
            },
        )


def test_optional_placeholder_allows_empty_value() -> None:
    """Declared-optional placeholder may be empty (a23318 first-run path)."""
    rendered = _render(
        _prompt_config(optional_placeholders=["existing_doc"]),
        {
            "subsystem_path": "systems/pipeline",
            "existing_doc": "",
            "inventory_json": '{"modules": []}',
        },
    )
    assert "## Existing document content (may be empty)\n\n" in rendered
    assert '{"modules": []}' in rendered


def test_optional_placeholder_allows_whitespace_only_value() -> None:
    rendered = _render(
        _prompt_config(optional_placeholders=["existing_doc"]),
        {
            "subsystem_path": "systems/pipeline",
            "existing_doc": "   \n",
            "inventory_json": '{"modules": []}',
        },
    )
    assert '{"modules": []}' in rendered


def test_optional_placeholder_still_requires_resolvability() -> None:
    """Optionality waives the non-empty check only — not resolution."""
    with pytest.raises(ValueError, match="unfilled placeholders"):
        _render(
            _prompt_config(optional_placeholders=["existing_doc"]),
            {
                "subsystem_path": "systems/pipeline",
                "inventory_json": '{"modules": []}',
            },
        )


def test_optional_placeholder_with_content_renders_normally() -> None:
    rendered = _render(
        _prompt_config(optional_placeholders=["existing_doc"]),
        {
            "subsystem_path": "systems/pipeline",
            "existing_doc": "<!-- AUTHORED -->\nPrior doc body.",
            "inventory_json": '{"modules": []}',
        },
    )
    assert "Prior doc body." in rendered


def test_strict_check_unaffected_for_other_placeholders() -> None:
    """Declaring one optional name does not relax sibling placeholders."""
    with pytest.raises(ValueError, match="'inventory_json' is empty or None"):
        _render(
            _prompt_config(optional_placeholders=["existing_doc"]),
            {
                "subsystem_path": "systems/pipeline",
                "existing_doc": "",
                "inventory_json": "   ",
            },
        )
