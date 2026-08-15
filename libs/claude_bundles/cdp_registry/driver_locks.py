"""Manage per-registration flock files and in-process driver-lock holds for exclusive CDP attachment ownership across workers."""

from __future__ import annotations

import contextlib
import fcntl
import os

from claude_bundles import cdp_registry_store as _store

from .models import RegistryBusyError
from .registry_module import registry_package


def _claim_driver_lock(registration_id: str) -> int:
    held_locks = registry_package()._HELD_LOCKS
    if registration_id in held_locks:
        raise RegistryBusyError(
            f"registration {registration_id!r} already held by this process"
        )
    fd = _store.open_lock(_store.registration_lock_path(registration_id))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        raise RegistryBusyError(
            f"registration {registration_id!r} already has an attached driver"
        ) from exc
    held_locks[registration_id] = fd
    return fd


def _release_driver_lock(registration_id: str) -> None:
    held_locks = registry_package()._HELD_LOCKS
    fd = held_locks.pop(registration_id, None)
    if fd is None:
        return
    with contextlib.suppress(OSError):
        fcntl.flock(fd, fcntl.LOCK_UN)
    with contextlib.suppress(OSError):
        os.close(fd)


def is_driver_lock_held(registration_id: str) -> bool:
    """True if another process holds the driver flock (LOCK_NB probe, no claim)."""
    lock_path = _store.registration_lock_path(registration_id)
    if not lock_path.exists():
        return False
    fd = _store.open_lock(lock_path)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return True
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def process_holds_driver_lock(registration_id: str) -> bool:
    """True iff this process claimed the driver lock via register/reattach."""
    return registration_id in registry_package()._HELD_LOCKS
