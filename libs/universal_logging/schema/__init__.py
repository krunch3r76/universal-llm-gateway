"""
Canonical log record schema for universal_logging.

INVARIANT: ∀ log_output: fields ⊆ CANONICAL_FIELDS
INVARIANT: ∀ renderer: operates_on(canonical_dict) ∧ ¬modifies_fields

This module defines THE schema. All renderers consume it.
No renderer may introduce, remove, or reinterpret fields.
"""

from .fields import CANONICAL_FIELDS, CallerInfo, ErrorInfo
from .record_builder import CanonicalRecordBuilder, build_canonical_record

__all__ = [
    "CanonicalRecordBuilder",
    "build_canonical_record",
    "CANONICAL_FIELDS",
    "CallerInfo",
    "ErrorInfo",
]
