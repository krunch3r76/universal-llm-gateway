"""Tool-search manifest overrides for the email overflow tool."""

from __future__ import annotations

from tool_search_manifest import ManifestEntry, apply_manifest_override
from tool_search_matcher import (
    _MANIFEST_OVERRIDES,
    _render_dispatch_template,
)


def test_email_dispatch_template_shows_nested_arguments() -> None:
    schema = {
        "properties": {
            "op": {"type": "string"},
            "arguments": {"type": "string", "default": "{}"},
        }
    }
    tpl = _render_dispatch_template("email", schema, ["review_extract"])
    assert '"arguments":' in tpl
    assert "message_id" in tpl
    assert "review_extract" in tpl


def test_email_manifest_override_populates_required_args() -> None:
    entry = ManifestEntry(
        name="email",
        purpose="Email tool.",
        dispatch_template="dispatch(tool=\"email\", arguments='{}')",
    )
    merged = apply_manifest_override(entry)
    ov = _MANIFEST_OVERRIDES["email"]
    assert merged.required_args_by_op == ov["required_args_by_op"]
    assert "review_extract" in merged.ops
    assert "create_folder" in merged.ops
    assert "nested" in merged.example or "arguments" in merged.example
