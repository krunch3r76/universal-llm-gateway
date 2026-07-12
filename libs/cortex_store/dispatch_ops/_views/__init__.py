"""Derived-view render helpers — recipe, core, stamps, archive."""

from .archive import archive_revision, read_asof_instance
from .recipe import load_recipe, parse_recipe_id, validate_recipe
from .render_core import (
    build_document_body,
    canonical_core_bytes,
    compute_core_hash,
    extract_core_sections,
    render_core_sections,
    snapshot_for_scope,
    validate_citation_grammar,
)
from .stamps import (
    build_stamp,
    check_attr_edge_parity,
    parse_stamp_from_body,
    view_registration_attrs,
)

__all__ = [
    "archive_revision",
    "build_document_body",
    "build_stamp",
    "canonical_core_bytes",
    "check_attr_edge_parity",
    "compute_core_hash",
    "extract_core_sections",
    "load_recipe",
    "parse_recipe_id",
    "parse_stamp_from_body",
    "read_asof_instance",
    "render_core_sections",
    "snapshot_for_scope",
    "validate_citation_grammar",
    "validate_recipe",
    "view_registration_attrs",
]
