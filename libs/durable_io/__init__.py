"""Serialised durable leaf for cortex-notes writes.

Cross-process serialisation is ``fcntl.flock`` on a sibling lockfile.
``threading.Lock`` does not hold across OS processes — see
``test_cross_process_serialisation.py``.
"""

from .atomic import (
    PreImageMismatchError,
    WriteVerifyError,
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
    "WriteVerifyError",
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
