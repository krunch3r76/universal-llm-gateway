"""CDP async digest extract backend — job-driven, not sync inline."""

from __future__ import annotations

from typing import Any


def extract_claims_cdp(
    entry_text: str,
    *,
    entry_anchor: str,
    journal_uri: str,
) -> dict[str, Any] | None:
    """CDP extract is async-only — callers must use digest_jobs.enqueue_extract."""
    raise RuntimeError(
        "CDP digest extract is async-only. Use digest_jobs.enqueue_extract and "
        "digest tick — not inline extract_claims."
    )
