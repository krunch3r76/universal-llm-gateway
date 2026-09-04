"""Durable atomic writes with post-persist verification.

Implementation lives in ``durable_io.atomic`` — the serialised leaf every
cortex-notes writer funnels through (flock + temp+fsync+replace + CAS).
This module keeps the historical ``tools._durable_write`` import path.
"""

from __future__ import annotations

from durable_io.atomic import (
    PreImageMismatchError,
    RmwResult,
    WriteVerifyError,
    durable_rmw_text,
    durable_write_bytes,
    durable_write_text,
    finalize_atomic_replace,
    lock_path_for,
    path_flock,
    retain_existing,
    temp_path_for,
    verify_persisted,
    write_verify_error_dict,
)

__all__ = [
    "PreImageMismatchError",
    "RmwResult",
    "WriteVerifyError",
    "durable_rmw_text",
    "durable_write_bytes",
    "durable_write_text",
    "finalize_atomic_replace",
    "lock_path_for",
    "path_flock",
    "retain_existing",
    "temp_path_for",
    "verify_persisted",
    "write_verify_error_dict",
]
