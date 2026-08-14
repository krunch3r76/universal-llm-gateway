"""Parse operator-pasted ClaimIt results — extract only, never infer hits.

JSON is the supported ingest. HTML is stored raw; hits are emitted only when
the paste contains an explicit `application/json` results block matching the
documented schema. Unparseable HTML yields `hits=[]` with kind `ingest_unparsed`,
which is not a completed zero-hit search.
"""

from __future__ import annotations

import json
import re
from typing import Any

from unclaimed_property_hunter.models import Hit

_JSON_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/json["\'][^>]*>(?P<body>.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)

_HIT_KEYS = (
    "property_id",
    "holder",
    "owner_name",
    "reported_address",
    "property_type",
    "amount_or_range",
    "escheat_or_report_date",
)


def _hit_from_mapping(row: dict[str, Any]) -> Hit | None:
    """Build a Hit when property_id is a non-empty string; otherwise skip."""
    pid = str(row.get("property_id") or row.get("propertyID") or "").strip()
    if not pid:
        return None
    return Hit(
        property_id=pid,
        holder=str(row.get("holder") or row.get("holderName") or "").strip(),
        owner_name=str(row.get("owner_name") or row.get("ownerName") or "").strip(),
        reported_address=str(
            row.get("reported_address") or row.get("address") or ""
        ).strip(),
        property_type=str(
            row.get("property_type") or row.get("propertyTypeDescription") or ""
        ).strip(),
        amount_or_range=str(
            row.get("amount_or_range") or row.get("propertyValue") or ""
        ).strip(),
        escheat_or_report_date=str(
            row.get("escheat_or_report_date") or row.get("reportDate") or ""
        ).strip(),
    )


def parse_json_hits(text: str) -> list[Hit]:
    """Parse a JSON object `{"hits":[...]}` or a bare list of hit objects.

    Raises ValueError when the payload is not JSON or has no hits array/list.
    An empty hits array is a completed zero-hit ingest (search_executed true).
    """
    payload = json.loads(text)
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict) and isinstance(payload.get("hits"), list):
        rows = payload["hits"]
    else:
        raise ValueError("JSON ingest requires {\"hits\": [...]} or a list of objects")
    hits: list[Hit] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each hit must be a JSON object")
        hit = _hit_from_mapping(row)
        if hit is None:
            raise ValueError("hit missing property_id/propertyID")
        hits.append(hit)
    return hits


def parse_html_hits(text: str) -> list[Hit] | None:
    """Return hits from an embedded JSON results block, or None if unparsed.

    None means 'do not treat this as a search result set.'
    """
    for match in _JSON_SCRIPT_RE.finditer(text):
        body = match.group("body").strip()
        try:
            return parse_json_hits(body)
        except (ValueError, json.JSONDecodeError):
            continue
    return None
