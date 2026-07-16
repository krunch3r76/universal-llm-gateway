"""Model load-status preflight for Stargate consult callers.

Uses the existing public surface
``GET /v1/models/{id}?include_status=true``
(``loaded`` / ``busy`` / ``loading`` / ``available``).

Cold-load of large local GGUFs (e.g. 26B) can block
``POST /v1/chat/completions`` for minutes with no progress signal.
Latency-sensitive one-shots should set ``require_warm=True`` and
optionally supply ``fallback_models``.
"""

from __future__ import annotations

import sys
from typing import Any

import httpx

WARM_STATUSES = frozenset({"loaded", "busy"})
COLD_STATUSES = frozenset({"loading", "available"})

DEFAULT_PROBE_TIMEOUT_S = 5.0


def probe_model_status(
    model_id: str,
    stargate_url: str,
    *,
    timeout: float = DEFAULT_PROBE_TIMEOUT_S,
) -> str | None:
    """Return aggregate status string, or None if the probe fails."""
    url = f"{stargate_url.rstrip('/')}/v1/models/{model_id}"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params={"include_status": "true"})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        status = data.get("status")
        return status if isinstance(status, str) else None
    except (httpx.HTTPError, ValueError, TypeError):
        return None


def resolve_ready_model(
    model_id: str,
    stargate_url: str,
    *,
    require_warm: bool = False,
    fallback_models: list[str] | None = None,
    probe_timeout: float = DEFAULT_PROBE_TIMEOUT_S,
) -> dict[str, Any]:
    """Pick a warm model or explain why the call should not proceed blind.

    Returns a dict with keys:
      - ``model_id``: selected id (may differ when a fallback wins)
      - ``status``: probe result for the selected id (or None)
      - ``skipped``: list of ``{model_id, status, reason}`` for rejected candidates
      - ``warning``: optional human-readable cold-load note (when proceeding cold)
    """
    candidates = [model_id, *(fallback_models or [])]
    skipped: list[dict[str, str]] = []
    first_status: str | None = None

    for mid in candidates:
        status = probe_model_status(mid, stargate_url, timeout=probe_timeout)
        if mid == model_id:
            first_status = status
        if status in WARM_STATUSES:
            return {
                "model_id": mid,
                "status": status,
                "skipped": skipped,
                "warning": None,
            }
        reason = (
            "probe_failed"
            if status is None
            else ("cold" if status in COLD_STATUSES else f"status={status}")
        )
        skipped.append({"model_id": mid, "status": status or "unknown", "reason": reason})

    if require_warm:
        detail = ", ".join(
            f"{s['model_id']}={s['status']}" for s in skipped
        ) or "no candidates"
        raise RuntimeError(
            "require_warm: no loaded/busy candidate "
            f"(probed: {detail}). Cold-load of large local GGUFs can block "
            "chat/completions for minutes — use a warm local seat, a cloud "
            "fallback, or omit --require-warm for intentional cold loads."
        )

    warning = None
    if first_status in COLD_STATUSES:
        warning = (
            f"model {model_id} status={first_status}: chat/completions may block "
            "on cold-load (large local GGUFs often take minutes). "
            "Pass require_warm=True with fallback_models for latency-sensitive passes."
        )
        print(f"  WARN: {warning}", file=sys.stderr)

    return {
        "model_id": model_id,
        "status": first_status,
        "skipped": skipped,
        "warning": warning,
    }
