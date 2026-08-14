"""CA SCO ClaimIt unclaimed-property hunter — dated evidentiary search.

Callers: the `scripts/unclaimed-property-hunt` one-shot and any later scheduler
that imports :func:`scheduler_seam.scheduled_entry`. Invariant: never synthesize
property records; a probe that cannot execute a search is not a zero-hit result.
"""

from unclaimed_property_hunter.models import Hit, Query, RunRecord

__all__ = ["Hit", "Query", "RunRecord"]
