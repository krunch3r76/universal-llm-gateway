"""Cortex dispatch op: doc_template — dense-spec authoring skeleton."""

from __future__ import annotations

from typing import Any

from implement_admission import dense_spec_schema as _schema

_SUPPORTED_DOC_TYPES: frozenset[str] = frozenset({"implement_dense_spec"})

_SECTION_HEADINGS: dict[str, str] = {
    "problem": "## Problem",
    "non_goals": "## Non-goals",
    "provenance": "## Provenance — source-of-truth",
    "touch_points": "## Touch-points",
    "forks": "## Bound design decisions",
    "implementation": "## Implementation guidance",
    "acceptance": "## Acceptance criteria",
    "verification": "## Verification (quality gates)",
}


def _section_block(key: str) -> str:
    heading = _SECTION_HEADINGS[key]
    hint = _schema._SECTION_ACCEPTED_PATTERNS[key]
    return f"{heading}\n\n<!-- accepted pattern: {hint} -->\n\n[Author content here.]\n"


def _build_implement_dense_spec_template() -> str:
    sections = "".join(_section_block(key) for key in _schema._REQUIRED_SECTIONS)
    return (
        "# Dense implement spec\n\n"
        f"{sections}\n"
        "<reasoning_trace>\n\n"
        "<!-- reasoning_trace tag block required; body must contain: no fork remains open -->\n\n"
        "Record bound forks resolved. No fork remains open.\n\n"
        "</reasoning_trace>\n\n"
        "## Post-fill attestation checklist\n\n"
        "1. Compute `spec_sha256:<hex>` via `dense_spec_hash_uri(filled_spec_text)`.\n"
        "2. Record or supersede a confirmed `implement_ready` assertion on the todo "
        "citing the dense-spec path and the exact `spec_sha256` token.\n"
        "3. Distill `files_expected` and `acceptance_criteria` onto the todo at "
        "Gate-2 close.\n"
    )


def _op_doc_template(
    doc_type: str = "implement_dense_spec", **_: object
) -> dict[str, Any]:
    """Return a dense-spec skeleton that round-trips validate_dense_spec when filled."""
    normalized = (doc_type or "").strip()
    if normalized not in _SUPPORTED_DOC_TYPES:
        supported = ", ".join(sorted(_SUPPORTED_DOC_TYPES))
        return {
            "error": f"unknown doc_type {doc_type!r}; supported: {supported}",
            "status_code": 422,
        }
    template = _build_implement_dense_spec_template()
    return {
        "ok": True,
        "doc_type": normalized,
        "template": template,
        "required_sections": list(_schema._REQUIRED_SECTIONS.keys()),
    }


__all__ = ["_SUPPORTED_DOC_TYPES", "_op_doc_template"]
