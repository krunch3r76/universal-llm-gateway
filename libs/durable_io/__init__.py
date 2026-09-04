"""Serialised durable leaf for cortex-notes writes.

Cross-process serialisation is ``fcntl.flock`` on a sibling lockfile.
``threading.Lock`` does not hold across OS processes — see
``test_cross_process_serialisation.py``.

Harvest nominates these manage slugs when this lib lands (package-grain).
Every process that imports the leaf is listed so restart derivation is
closed rather than unmapped.
"""

from .atomic import (
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

CONSUMERS: tuple[str, ...] = (
    "cdp_ask",
    "cortex_api",
    "git_integration_worker",
    "mcp",
    "stargate",
)

__all__ = [
    "CONSUMERS",
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
