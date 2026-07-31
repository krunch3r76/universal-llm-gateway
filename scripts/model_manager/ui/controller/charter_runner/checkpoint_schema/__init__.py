"""Checkpoint footer + body resolve + prose parse (Phase 3 absorb of parse trio)."""

from __future__ import annotations

from .body import (
    extract_sidecar_uri,
    materialize_checkpoint_turn,
    normalize_checkpoint_machine_fields,
    resolve_checkpoint_body,
    strip_sidecar_frontmatter,
)
from .footer import (
    EMPTY_GATED_PICKUP_SENTINEL,
    FOOTER_FENCE,
    FooterFields,
    ValidationResult,
    append_footer_to_packet,
    emit_footer,
    footer_kwargs_for_window,
    is_exhausted_hopper_footer,
    output_format_footer_requirement,
    validate_checkpoint_footer,
)
from .parse import (
    _NONE_WINDOW_RE,
    ParsedCheckpoint,
    Step,
    _wip_is_none,
    first_actionable_step,
    item_is_gated,
    parse_checkpoint,
    pickup_detent,
)
from .sections import (
    aggregate_what_happened_plain,
    extract_remaining_work,
    extract_what_happened_plain,
    find_section,
    split_sections,
)

__all__ = [
    "EMPTY_GATED_PICKUP_SENTINEL",
    "FOOTER_FENCE",
    "FooterFields",
    "ParsedCheckpoint",
    "Step",
    "ValidationResult",
    "_NONE_WINDOW_RE",
    "_wip_is_none",
    "aggregate_what_happened_plain",
    "append_footer_to_packet",
    "emit_footer",
    "extract_remaining_work",
    "extract_sidecar_uri",
    "extract_what_happened_plain",
    "find_section",
    "first_actionable_step",
    "footer_kwargs_for_window",
    "is_exhausted_hopper_footer",
    "item_is_gated",
    "materialize_checkpoint_turn",
    "normalize_checkpoint_machine_fields",
    "output_format_footer_requirement",
    "parse_checkpoint",
    "pickup_detent",
    "resolve_checkpoint_body",
    "split_sections",
    "strip_sidecar_frontmatter",
    "validate_checkpoint_footer",
]
