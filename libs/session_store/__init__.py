"""Session transcript store — parse/render, distill, seal."""

from session_store.distill import distill_index_line
from session_store.envelope import seal
from session_store.fence import (
    choose_fence_char,
    closing_fence,
    extract_fenced,
    fence_length,
    opening_fence,
    wrap_fenced,
)
from session_store.models import (
    AttachmentRef,
    Budget,
    EnvelopeOverBudget,
    ImmutableArchiveError,
    InvalidRefError,
    SchemaError,
    SealResult,
    SessionDoc,
    SessionForSeal,
    SessionStoreError,
    Turn,
)
from session_store.schema import (
    assert_mutable,
    parse_transcript,
    render_transcript,
    section_count,
)

__all__ = [
    "AttachmentRef",
    "Budget",
    "EnvelopeOverBudget",
    "ImmutableArchiveError",
    "InvalidRefError",
    "SchemaError",
    "SealResult",
    "SessionDoc",
    "SessionForSeal",
    "SessionStoreError",
    "Turn",
    "assert_mutable",
    "choose_fence_char",
    "closing_fence",
    "distill_index_line",
    "extract_fenced",
    "fence_length",
    "opening_fence",
    "parse_transcript",
    "render_transcript",
    "seal",
    "section_count",
    "wrap_fenced",
]
