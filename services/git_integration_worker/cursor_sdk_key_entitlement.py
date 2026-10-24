"""Cursor key entitlement cache for cursor-sdk admit.

Two signals fill one process-local set of dead provenances:

* Startup walks each configured key once and applies a probe verdict.
  The default probe returns ``unknown`` and does not spawn a bridge — the
  usage-entitlement script answers a different question, and a boot-time
  agent create is the burn this gate exists to stop. Tests and a future
  RPC inject a probe that returns ``not_entitled`` or ``entitled``.
* The first bridge abort whose text contains ``plan_required`` marks that
  key dead. A later probe verdict of ``entitled`` clears it. Process restart
  drops the set. A mark made with a known key value also clears when a later
  check presents a different key for the same provenance, so rotating
  ``~/.gateway/secrets.env`` does not wait on a worker recycle.

Admit consults the set before dispatch HOME mint or bridge spawn.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from universal_logging import get_logger

ProbeVerdict = Literal["entitled", "not_entitled", "unknown"]
ProbeFn = Callable[..., ProbeVerdict]

logger = get_logger(__name__)

_LOCK = threading.Lock()
# provenance -> sha256 of the key that was marked, or None when the value
# was unknown. A later check with a different key drops the entry.
_NOT_ENTITLED: dict[str, str | None] = {}
_PLAN_REQUIRED = "plan_required"
_ENV_KEY_NAMES = frozenset({"CURSOR_API_KEY", "CURSOR_API_KEY_OTHER_MODELS"})


def _fingerprint(api_key: str | None) -> str | None:
    if not api_key:
        return None
    return hashlib.sha256(api_key.encode()).hexdigest()


def _live_key_for_provenance(provenance: str) -> str | None:
    """Current secrets.env-overlaid value for an ``env:`` provenance, if any."""
    from services.git_integration_worker.cursor_sdk_context import (
        effective_cursor_key_env,
    )

    if not provenance.startswith("env:"):
        return None
    var = provenance[len("env:") :]
    if var not in _ENV_KEY_NAMES:
        return None
    value = effective_cursor_key_env().get(var, "").strip()
    return value or None


def clear_entitlement_cache() -> None:
    """Drop every dead-key mark. Tests and process restart only."""
    with _LOCK:
        _NOT_ENTITLED.clear()


def mark_not_entitled(provenance: str, *, api_key: str | None = None) -> None:
    """Remember that *provenance* must not admit until clear or key rotation.

    When *api_key* is omitted, the live ``secrets.env`` overlay is snapshotted
    so a later check can tell a rotated value from the key that failed.
    The raw key is not logged.
    """
    key = provenance.strip()
    if not key:
        return
    resolved = api_key if api_key is not None else _live_key_for_provenance(key)
    fingerprint = _fingerprint(resolved)
    with _LOCK:
        _NOT_ENTITLED[key] = fingerprint
    logger.warning("cursor key marked not entitled provenance=%s", key)


def clear_not_entitled(provenance: str) -> None:
    """Forget a dead mark after a probe reports the key entitled."""
    key = provenance.strip()
    if not key:
        return
    with _LOCK:
        _NOT_ENTITLED.pop(key, None)


def is_not_entitled(provenance: str, *, api_key: str | None = None) -> bool:
    """True when admit must refuse this provenance before HOME or bridge.

    Passing *api_key* compares its digest to the digest stored at mark time.
    A mismatch means the key was rotated: the mark is dropped and admit may
    proceed. Omitting *api_key* does not clear a fingerprinted mark.
    """
    key = provenance.strip()
    if not key:
        return False
    current = _fingerprint(api_key)
    with _LOCK:
        if key not in _NOT_ENTITLED:
            return False
        stored = _NOT_ENTITLED[key]
        if stored is not None and current is not None and stored != current:
            del _NOT_ENTITLED[key]
            return False
        return True


def note_plan_required(
    *texts: str,
    provenance: str,
    api_key: str | None = None,
) -> bool:
    """Mark *provenance* dead when any text carries the plan_required wall.

    *api_key* is optional. When it is set, the mark is bound to that value so
    a later check with a different key clears the block. Returns True when
    the mark was applied.
    """
    blob = "\n".join(text for text in texts if text).lower()
    if _PLAN_REQUIRED not in blob:
        return False
    if not provenance.strip():
        return False
    mark_not_entitled(provenance, api_key=api_key)
    return True


def apply_probe_verdict(
    provenance: str,
    verdict: str,
    *,
    api_key: str | None = None,
) -> None:
    """Fold one probe result into the cache. ``unknown`` leaves the mark.

    *api_key* binds a ``not_entitled`` verdict to that key's digest. An
    ``entitled`` verdict clears the provenance regardless of key.
    """
    if verdict == "not_entitled":
        mark_not_entitled(provenance, api_key=api_key)
    elif verdict == "entitled":
        clear_not_entitled(provenance)


def configured_key_provenances(
    *,
    real_home: Path | str | None = None,
) -> list[tuple[str, str | None]]:
    """Provenance labels for keys in the live secrets overlay.

    Primary pool is ``CURSOR_API_KEY``. Other Models is the secondary env.
    Values come from ``~/.gateway/secrets.env`` over ``os.environ``, so a
    file edit is visible without recycling the worker. Absence is not a
    dead mark — parity already fails closed on a missing key.
    """
    from services.git_integration_worker.cursor_sdk_context import (
        effective_cursor_key_env,
    )

    environ = effective_cursor_key_env(real_home=real_home)
    rows: list[tuple[str, str | None]] = []
    primary = environ.get("CURSOR_API_KEY", "").strip()
    if primary:
        rows.append(("env:CURSOR_API_KEY", primary))
    secondary_name = "CURSOR_API_KEY_OTHER_MODELS"
    secondary = environ.get(secondary_name, "").strip()
    if secondary:
        rows.append((f"env:{secondary_name}", secondary))
    return rows


def default_startup_probe(provenance: str, *, api_key: str | None) -> ProbeVerdict:
    """No network and no bridge. ``unknown`` does not mark the key dead."""
    del provenance, api_key
    return "unknown"


def probe_configured_keys(
    *,
    probe: ProbeFn | None = None,
    real_home: Path | str | None = None,
) -> list[dict[str, str]]:
    """Probe each configured key once and cache pass/fail.

    Called from GIW startup persistence (after the health port is bound).
    The default probe does not spawn a bridge. *real_home* selects which
    ``secrets.env`` overlay the probe reads; production leaves it unset.
    """
    fn = probe or default_startup_probe
    results: list[dict[str, str]] = []
    for provenance, api_key in configured_key_provenances(real_home=real_home):
        try:
            verdict = fn(provenance, api_key=api_key)
        except Exception as exc:  # noqa: BLE001 — a probe fault must not kill boot
            logger.warning(
                "cursor key entitlement probe failed provenance=%s err=%s",
                provenance,
                exc,
            )
            verdict = "unknown"
        if verdict not in ("entitled", "not_entitled", "unknown"):
            verdict = "unknown"
        apply_probe_verdict(provenance, verdict, api_key=api_key)
        results.append({"provenance": provenance, "verdict": verdict})
    return results
