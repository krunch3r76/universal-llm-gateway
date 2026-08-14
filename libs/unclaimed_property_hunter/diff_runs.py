"""Diff two hunt runs by property_id — new, disappeared, and field-level changes.

Used after ingest N vs N-1. Probe runs with search_executed=false still diff
as empty hit sets so a later real ingest shows every property as added.
"""

from __future__ import annotations

from dataclasses import dataclass

from unclaimed_property_hunter.models import Hit, RunRecord


@dataclass(frozen=True)
class HitChange:
    """One property_id whose normalized fields differed across two runs."""

    property_id: str
    before: dict[str, str]
    after: dict[str, str]


@dataclass(frozen=True)
class RunDiff:
    """Set-level and field-level delta between run N-1 and run N."""

    added: list[str]
    disappeared: list[str]
    changed: list[HitChange]


def _hit_fields(hit: Hit) -> dict[str, str]:
    return {
        "holder": hit.holder,
        "owner_name": hit.owner_name,
        "reported_address": hit.reported_address,
        "property_type": hit.property_type,
        "amount_or_range": hit.amount_or_range,
        "escheat_or_report_date": hit.escheat_or_report_date,
    }


def diff_runs(previous: RunRecord, current: RunRecord) -> RunDiff:
    """Compare normalized hits using property_id as the only identity key."""
    prev_map = {hit.property_id: hit for hit in previous.hits}
    curr_map = {hit.property_id: hit for hit in current.hits}
    added = sorted(set(curr_map) - set(prev_map))
    disappeared = sorted(set(prev_map) - set(curr_map))
    changed: list[HitChange] = []
    for pid in sorted(set(prev_map) & set(curr_map)):
        before = _hit_fields(prev_map[pid])
        after = _hit_fields(curr_map[pid])
        if before != after:
            changed.append(HitChange(property_id=pid, before=before, after=after))
    return RunDiff(added=added, disappeared=disappeared, changed=changed)
