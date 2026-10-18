from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(slots=True)
class Snapshot:
    as_of_utc: datetime
    healthy: bool
    committed_usd: Decimal
    open_reservations_usd: Decimal
    window_start: str
    ledger_instance_id: str
    latch_active: bool
    last_entries: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Passage:
    source: str
    rank: int
    text: str


@dataclass(slots=True)
class SearchResult:
    search_id: str
    scope: str
    query: str
    weak_match: bool | None
    rerank_status: str | None
    top_ce: float | None
    passages: list[Passage]
    dropped: bool = False
    drop_reason: str | None = None


@dataclass(slots=True)
class ParsedContract:
    answer: str
    ledger: str
    next_queries: list[tuple[str, str]]
    status: str
    extra_query_lines: int
    ok: bool
    error: str | None = None


@dataclass(slots=True)
class SendResult:
    ok: bool
    status_code: int | None
    text: str
    usage: dict[str, Any]
    stop_reason: str | None
    message_id: str | None
    error_body: str | None = None
    gate_refusal: bool = False
    usage_incomplete: bool = False
    delta_usage_seen: bool = False
    parse_error: bool = False
    usage_skipped_dict_keys: list[str] | None = None
    usage_invalid_keys: list[str] | None = None
