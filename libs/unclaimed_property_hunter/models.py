"""Normalized query, hit, and run records for CA SCO ClaimIt provenance.

These dataclasses are the only shape the Cortex writer and the diff engine
accept. Empty `hits` is legal only when `search_executed` is true (a completed
search returned nothing) or when ingest could not parse — never as a stand-in
for "we did not search."
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

RunKind = Literal[
    "transport_probe",
    "bulk_extract",
    "ingest_json",
    "ingest_html",
    "ingest_unparsed",
]


@dataclass(frozen=True)
class Query:
    """Surname-first search the operator asked the site to run.

    `exact_http_request` is the bytes-on-the-wire request this process sent
    (may be a landing-page GET). `intended_query_string` is lastName plus
    optional filters even when Turnstile blocked transmission.
    """

    surname: str
    first_name: str = ""
    city: str = ""
    intended_query_string: str = ""
    exact_http_request: str = ""
    endpoint_url: str = ""


@dataclass(frozen=True)
class Hit:
    """One SCO property row as reported — no inferred fields.

    Missing site fields stay empty strings. `holder` is flagged separately
    when it matches Prudential.
    """

    property_id: str
    holder: str = ""
    owner_name: str = ""
    reported_address: str = ""
    property_type: str = ""
    amount_or_range: str = ""
    escheat_or_report_date: str = ""

    def is_prudential(self) -> bool:
        """True when the holder string names Prudential (case-insensitive)."""
        return "prudential" in self.holder.lower()


@dataclass
class RunRecord:
    """One dated hunt: query, raw payload pointer, normalized hits, outcome."""

    run_id: str
    utc_timestamp: str
    query: Query
    run_kind: RunKind
    search_executed: bool
    raw_payload_uri: str
    raw_sha256: str
    hits: list[Hit] = field(default_factory=list)
    notes: str = ""

    def to_json_dict(self) -> dict[str, Any]:
        """Serialize for the normalized sidecar (no property records invented)."""
        payload = asdict(self)
        payload["hit_count"] = len(self.hits)
        payload["prudential_hits"] = [h.property_id for h in self.hits if h.is_prudential()]
        return payload
