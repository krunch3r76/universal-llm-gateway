"""Durable atomic writes with post-persist verification.

Implementation lives in ``durable_io.atomic`` — the serialised leaf every
cortex-notes writer funnels through (flock + temp+fsync+replace + CAS).
This module keeps the historical ``tools._durable_write`` import path.
"""

from __future__ import annotations

from typing import Any

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


class HouseRulesSizeRejectedError(Exception):
    """Raised when a house-rules post-edit image fails the hop byte gate.

    Callers inside ``path_flock`` turn this into a structured fs refusal without
    persisting bytes. Defined in ``tools._durable_write`` so both ``tools.file_editor``
    and ``tools.filesystem._write_authority`` can import it at module load without
    entering the ``tools.filesystem.__init__`` → ``_ops_text`` → ``file_editor`` cycle.
    """

    def __init__(self, rejection: dict[str, Any]) -> None:
        self.rejection = rejection
        super().__init__(str(rejection.get("error")))


__all__ = [
    "HouseRulesSizeRejectedError",
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
