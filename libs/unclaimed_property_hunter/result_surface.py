"""Shared result representation — one schema for CLI, sidecars, cortex, stderr.

Invariant: ``hit_count`` is an ``int`` only when ``search_executed`` is true.
When the search did not run, ``hit_count`` is ``None`` and
``execution_block_reason`` names why — never conflate with zero hits.
"""

from __future__ import annotations

from typing import Any, Literal

from unclaimed_property_hunter.models import CorpusFingerprint, RunKind, RunRecord
from unclaimed_property_hunter.surfaces import Surface

ExecutionBlockReason = Literal[
    "requires_js_cloudflare_turnstile_sws_session",
    "ingest_unparsed",
    "search_not_executed",
]

HitCountMeaning = Literal["completed_search", "not_a_completed_search"]


def execution_block_reason(
    run_kind: RunKind, search_executed: bool
) -> ExecutionBlockReason | None:
    """Map a non-executed run to a stable reason token for operator surfaces."""
    if search_executed:
        return None
    if run_kind == "transport_probe":
        return "requires_js_cloudflare_turnstile_sws_session"
    if run_kind == "ingest_unparsed":
        return "ingest_unparsed"
    return "search_not_executed"


def hit_count_meaning(search_executed: bool) -> HitCountMeaning:
    """Discriminant paired with ``hit_count`` so readers need not infer."""
    return "completed_search" if search_executed else "not_a_completed_search"


def resolved_hit_count(record: RunRecord) -> int | None:
    """Return hit count only for completed searches; ``None`` otherwise."""
    if not record.search_executed:
        return None
    return len(record.hits)


def public_run_dict(record: RunRecord) -> dict[str, Any]:
    """Normalized public projection for sidecars, CLI JSON, and aggregators."""
    reason = execution_block_reason(record.run_kind, record.search_executed)
    count = resolved_hit_count(record)
    return {
        "run_id": record.run_id,
        "utc_timestamp": record.utc_timestamp,
        "query": {
            "surname": record.query.surname,
            "first_name": record.query.first_name,
            "city": record.query.city,
            "intended_query_string": record.query.intended_query_string,
            "exact_http_request": record.query.exact_http_request,
            "endpoint_url": record.query.endpoint_url,
        },
        "run_kind": record.run_kind,
        "search_executed": record.search_executed,
        "hit_count": count,
        "verdict": verdict_token(search_executed=record.search_executed, hit_count=count),
        "hit_count_meaning": hit_count_meaning(record.search_executed),
        "execution_block_reason": reason,
        "hits": [
            {
                "property_id": h.property_id,
                "holder": h.holder,
                "owner_name": h.owner_name,
                "reported_address": h.reported_address,
                "property_type": h.property_type,
                "amount_or_range": h.amount_or_range,
                "escheat_or_report_date": h.escheat_or_report_date,
            }
            for h in record.hits
        ],
        "prudential_hits": [h.property_id for h in record.hits if h.is_prudential()],
        "raw_payload_uri": record.raw_payload_uri,
        "raw_sha256": record.raw_sha256,
        "notes": record.notes,
        "check_failed": record.check_failed,
        "notify_outcome": record.notify_outcome,
        "corpus_fingerprint": _fingerprint_dict(record.corpus_fingerprint),
    }


def _fingerprint_dict(fp: CorpusFingerprint | None) -> dict[str, Any] | None:
    if fp is None:
        return None
    return {
        "url": fp.url,
        "last_modified": fp.last_modified,
        "etag": fp.etag,
        "content_length": fp.content_length,
        "zip_sha256": fp.zip_sha256,
        "rows_scanned": fp.rows_scanned,
    }


def format_entity_description(record: RunRecord) -> str:
    """Cortex run-document description — never ``hits=0`` when search did not run."""
    reason = execution_block_reason(record.run_kind, record.search_executed)
    if record.search_executed:
        return (
            f"{record.run_kind} search_executed=True "
            f"hit_count={len(record.hits)}"
        )
    return (
        f"{record.run_kind} search_executed=False "
        f"hit_count=N/A execution_block_reason={reason}"
    )


def format_entity_attributes(record: RunRecord) -> dict[str, Any]:
    """Cortex entity attributes mirroring the type-level hit_count rule."""
    attrs: dict[str, Any] = {
        "surname": record.query.surname,
        "run_kind": record.run_kind,
        "search_executed": record.search_executed,
        "utc_timestamp": record.utc_timestamp,
        "intended_query_string": record.query.intended_query_string,
        "exact_http_request": record.query.exact_http_request,
        "endpoint_url": record.query.endpoint_url,
        "hit_count_meaning": hit_count_meaning(record.search_executed),
    }
    count = resolved_hit_count(record)
    reason = execution_block_reason(record.run_kind, record.search_executed)
    if count is None:
        attrs["hit_count"] = None
        attrs["execution_block_reason"] = reason
    else:
        attrs["hit_count"] = count
        attrs["execution_block_reason"] = None
    if record.corpus_fingerprint is not None:
        attrs["corpus_fingerprint"] = _fingerprint_dict(record.corpus_fingerprint)
    if record.notify_outcome is not None:
        attrs["notify_outcome"] = record.notify_outcome
    attrs["check_failed"] = record.check_failed
    return attrs


def format_assertion_claim(record: RunRecord) -> str:
    """Run assertion text — explicit that non-executed ≠ zero-hit."""
    reason = execution_block_reason(record.run_kind, record.search_executed)
    if record.search_executed:
        return (
            f"CA SCO hunt {record.run_kind} for surname {record.query.surname}: "
            f"search_executed=True hit_count={len(record.hits)} "
            f"(completed search; zero is a valid outcome)."
        )
    return (
        f"CA SCO hunt {record.run_kind} for surname {record.query.surname}: "
        f"search_executed=False execution_block_reason={reason}. "
        f"NOT a zero-hit search — the site search did not complete."
    )


def verdict_token(
    *,
    search_executed: bool,
    hit_count: int | None,
    field_absent: bool = False,
) -> str:
    """Four-token honesty legend used by dated-search artifacts.

    ``NOT-RETRIEVED`` is for a field or surface that cannot be searched.
    ``NOT EXECUTED`` is a probe or a missing run — never a silent null.
    Zero hits after a completed search is ``EXECUTED ZERO``, not absence.
    """
    if field_absent:
        return "NOT-RETRIEVED"
    if not search_executed or hit_count is None:
        return "NOT EXECUTED"
    if hit_count == 0:
        return "EXECUTED ZERO"
    return f"EXECUTED HITS {hit_count}"


def surface_report_row(
    surface: Surface, record: RunRecord | None
) -> dict[str, object]:
    """One catalog surface plus last-run verdict — what a later seat can trust."""
    if record is None:
        token = verdict_token(search_executed=False, hit_count=None)
        executed = False
        hit_count: int | None = None
        run_id = None
        run_kind = None
    else:
        executed = record.search_executed
        hit_count = resolved_hit_count(record)
        token = verdict_token(search_executed=executed, hit_count=hit_count)
        run_id = record.run_id
        run_kind = record.run_kind
    return {
        "surface_id": surface.id,
        "name": surface.name,
        "url": surface.url,
        "gate": surface.gate,
        "automate": surface.automate,
        "verdict": token,
        "search_executed": executed,
        "hit_count": hit_count,
        "cannot_reach": list(surface.cannot_reach),
        "refresh": surface.refresh,
        "run_id": run_id,
        "run_kind": run_kind,
    }


def format_operator_stderr(record: RunRecord) -> str:
    """One-line operator-visible signal when the search did not execute."""
    reason = execution_block_reason(record.run_kind, record.search_executed)
    if record.search_executed:
        return ""
    return f"SEARCH NOT EXECUTED: reason={reason}"
