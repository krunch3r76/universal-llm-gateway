"""Cursor key entitlement cache for cursor-sdk admit.

Two signals fill one process-local set of dead provenances:

* Startup walks each configured key once and applies a probe verdict.
  The default probe returns ``unknown`` and does not spawn a bridge — the
  usage-entitlement script answers a different question, and a boot-time
  agent create is the burn this gate exists to stop. Tests and a future
  RPC inject a probe that returns ``not_entitled`` or ``entitled``.
* The first bridge abort whose text contains ``plan_required`` marks that
  key dead. A later probe verdict of ``entitled`` clears it. Process restart
  drops the set.

Admit consults the set before dispatch HOME mint or bridge spawn.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from typing import Literal

from universal_logging import get_logger

ProbeVerdict = Literal["entitled", "not_entitled", "unknown"]
ProbeFn = Callable[..., ProbeVerdict]

logger = get_logger(__name__)

_LOCK = threading.Lock()
_NOT_ENTITLED: set[str] = set()
_PLAN_REQUIRED = "plan_required"


def clear_entitlement_cache() -> None:
    """Drop every dead-key mark. Tests and process restart only."""
    with _LOCK:
        _NOT_ENTITLED.clear()


def mark_not_entitled(provenance: str) -> None:
    """Remember that *provenance* must not admit until restart or a clear."""
    key = provenance.strip()
    if not key:
        return
    with _LOCK:
        _NOT_ENTITLED.add(key)
    logger.warning("cursor key marked not entitled provenance=%s", key)


def clear_not_entitled(provenance: str) -> None:
    """Forget a dead mark after a probe reports the key entitled."""
    key = provenance.strip()
    if not key:
        return
    with _LOCK:
        _NOT_ENTITLED.discard(key)


def is_not_entitled(provenance: str) -> bool:
    """True when admit must refuse this provenance before HOME or bridge."""
    key = provenance.strip()
    if not key:
        return False
    with _LOCK:
        return key in _NOT_ENTITLED


def note_plan_required(*texts: str, provenance: str) -> bool:
    """Mark *provenance* dead when any text carries the plan_required wall.

    Returns True when the mark was applied.
    """
    blob = "\n".join(text for text in texts if text).lower()
    if _PLAN_REQUIRED not in blob:
        return False
    if not provenance.strip():
        return False
    mark_not_entitled(provenance)
    return True


def apply_probe_verdict(provenance: str, verdict: str) -> None:
    """Fold one probe result into the cache. ``unknown`` leaves the mark."""
    if verdict == "not_entitled":
        mark_not_entitled(provenance)
    elif verdict == "entitled":
        clear_not_entitled(provenance)


def configured_key_provenances() -> list[tuple[str, str | None]]:
    """Provenance labels for keys present in this process environment.

    Primary pool is ``CURSOR_API_KEY``. Other Models is the secondary env.
    Absence is not a dead mark — parity already fails closed on a missing key.
    """
    rows: list[tuple[str, str | None]] = []
    primary = os.environ.get("CURSOR_API_KEY", "").strip()
    if primary:
        rows.append(("env:CURSOR_API_KEY", primary))
    secondary_name = "CURSOR_API_KEY_OTHER_MODELS"
    secondary = os.environ.get(secondary_name, "").strip()
    if secondary:
        rows.append((f"env:{secondary_name}", secondary))
    return rows


def default_startup_probe(provenance: str, *, api_key: str | None) -> ProbeVerdict:
    """No network and no bridge. ``unknown`` does not mark the key dead."""
    del provenance, api_key
    return "unknown"


def probe_configured_keys(*, probe: ProbeFn | None = None) -> list[dict[str, str]]:
    """Probe each configured key once and cache pass/fail.

    Called from GIW startup persistence (after the health port is bound).
    The default probe does not spawn a bridge.
    """
    fn = probe or default_startup_probe
    results: list[dict[str, str]] = []
    for provenance, api_key in configured_key_provenances():
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
        apply_probe_verdict(provenance, verdict)
        results.append({"provenance": provenance, "verdict": verdict})
    return results
