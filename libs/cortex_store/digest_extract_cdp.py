"""CDP async digest extract backend (Fable 5329 — infrastructure stub).

Full job queue lives in digest_jobs.py (future). This module is the seam
behind CORTEX_DIGEST_EXTRACT_BACKEND=cdp.
"""

from __future__ import annotations

from typing import Any


def extract_claims_cdp(
    entry_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    """Async CDP extract — not yet implemented."""
    raise NotImplementedError(
        "CDP digest extract backend is not implemented yet. "
        "Use CORTEX_DIGEST_EXTRACT_BACKEND=stargate for sync dogfood, or "
        "implement digest_jobs.py + journal_digest_extract_cdp per "
        "5329-fable-cdp-digest-architecture-answer.md"
    )
