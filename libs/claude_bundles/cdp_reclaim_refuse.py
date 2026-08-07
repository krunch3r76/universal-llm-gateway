"""CSE by-id reclaim refuse — code gate (arc 6893 safety bind).

Census prose named ``cse_016UZLY…`` never-reclaim; classify + any reclaim
actuator MUST call these predicates. Env ``CDP_RECLAIM_REFUSE_CSE_IDS`` adds
comma-separated CSE ids (``cse_…``) without redeploy.
"""

from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

# Literal bind from 6893 ghost-CSE reclaim census / operator seat survival.
_DEFAULT_REFUSE_CSE_IDS: frozenset[str] = frozenset(
    {
        "cse_016UZLY1LHLTyTQG7dAH1eW8",
    }
)

_CSE_ID_RE = re.compile(r"(cse_[A-Za-z0-9]+)")


def cse_id_from_url(url: str) -> str | None:
    """Extract ``cse_*`` id from a Cowork CSE URL or bare id string."""
    raw = (url or "").strip()
    if not raw:
        return None
    if raw.startswith("cse_") and "/" not in raw and "?" not in raw:
        return raw
    path = urlsplit(raw).path or raw
    match = _CSE_ID_RE.search(path)
    return match.group(1) if match else None


def reclaim_refuse_cse_ids() -> frozenset[str]:
    """Default bind ∪ ``CDP_RECLAIM_REFUSE_CSE_IDS`` extras."""
    ids = set(_DEFAULT_REFUSE_CSE_IDS)
    raw = os.environ.get("CDP_RECLAIM_REFUSE_CSE_IDS", "").strip()
    if raw:
        for part in raw.split(","):
            token = part.strip()
            if not token:
                continue
            cse_id = cse_id_from_url(token) or token
            if cse_id.startswith("cse_"):
                ids.add(cse_id)
    return frozenset(ids)


def is_reclaim_refused_by_id(url_or_cse_id: str) -> bool:
    """True when URL/id matches the by-id never-reclaim allowlist."""
    cse_id = cse_id_from_url(url_or_cse_id)
    if cse_id is None:
        return False
    return cse_id in reclaim_refuse_cse_ids()


def reclaim_refuse_reason(url_or_cse_id: str) -> str | None:
    """Return ``by_id_refuse:<cse_id>`` when refused, else None."""
    cse_id = cse_id_from_url(url_or_cse_id)
    if cse_id is None or cse_id not in reclaim_refuse_cse_ids():
        return None
    return f"by_id_refuse:{cse_id}"


def guard_cse_reclaim(url_or_cse_id: str) -> str | None:
    """Actuator entry: refuse reason string, or None when reclaim may proceed.

    Future S3 CSE-close MUST call this before any closeTarget / kill. Classify
    uses the same predicate so emit-only scans never mark these closable.
    """
    return reclaim_refuse_reason(url_or_cse_id)
