"""Statement type schemas and ingestion helpers for the finance pipeline.

Phase 2: schema dicts describing expected JSON shape per statement type.
Phase 3: issuer slug resolution and assertion builders for Cortex ingestion.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

VALID_TYPES = frozenset({"checking", "credit_card", "utility", "phone", "ploc"})

STATEMENT_SCHEMAS: dict[str, dict[str, Any]] = {
    "credit_card": {
        "issuer": "string",
        "account_last4": "string",
        "statement_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "statement_date": "YYYY-MM-DD",
        "previous_balance": 0.0,
        "payments_total": 0.0,
        "charges_total": 0.0,
        "fees_total": 0.0,
        "interest_total": 0.0,
        "new_balance": 0.0,
        "minimum_due": 0.0,
        "due_date": "YYYY-MM-DD",
        "credit_limit": 0,
        "interest_rates": [
            {
                "type": "purchases|cash_advances|balance_transfer",
                "apr": 0.0,
                "balance_subject": 0.0,
                "interest_charged": 0.0,
            }
        ],
        "transactions": [
            {
                "date": "YYYY-MM-DD",
                "post_date": "YYYY-MM-DD",
                "description": "string",
                "amount": 0.0,
                "type": "payment|purchase|fee|interest|credit",
            }
        ],
    },
    "checking": {
        "bank": "string",
        "account_last4": "string",
        "statement_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "opening_balance": 0.0,
        "closing_balance": 0.0,
        "deposits_total": 0.0,
        "withdrawals_total": 0.0,
        "fees_total": 0.0,
        "transactions": [
            {
                "date": "YYYY-MM-DD",
                "description": "string",
                "amount": 0.0,
                "type": "deposit|withdrawal|fee|transfer|check",
                "running_balance": 0.0,
                "check_number": None,
            }
        ],
    },
    "utility": {
        "provider": "string",
        "account_number": "string",
        "billing_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "statement_date": "YYYY-MM-DD",
        "previous_balance": 0.0,
        "payments_received": 0.0,
        "current_charges": 0.0,
        "amount_due": 0.0,
        "due_date": "YYYY-MM-DD",
        "usage": {"electric_kwh": None, "gas_therms": None},
        "line_items": [{"description": "string", "amount": 0.0}],
    },
    "phone": {
        "provider": "string",
        "account_number": "string",
        "billing_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "statement_date": "YYYY-MM-DD",
        "previous_balance": 0.0,
        "payments_received": 0.0,
        "current_charges": 0.0,
        "amount_due": 0.0,
        "due_date": "YYYY-MM-DD",
        "lines": [{"number": "string", "charges": 0.0}],
    },
    "ploc": {
        "issuer": "string",
        "account_number": "string",
        "statement_date": "YYYY-MM-DD",
        "credit_limit": 0.0,
        "previous_balance": 0.0,
        "payments_total": 0.0,
        "advances_total": 0.0,
        "interest_total": 0.0,
        "new_balance": 0.0,
        "minimum_due": 0.0,
        "due_date": "YYYY-MM-DD",
        "interest_rates": [
            {
                "apr": 0.0,
                "balance_subject": 0.0,
                "interest_charged": 0.0,
                "days_in_cycle": 0,
            }
        ],
        "transactions": [
            {
                "date": "YYYY-MM-DD",
                "description": "string",
                "amount": 0.0,
                "type": "payment|advance|interest|fee",
            }
        ],
    },
}


# -- Phase 3: Cortex ingestion helpers --------------------------------------

ISSUER_SLUGS: dict[str, str] = {
    "wells fargo": "wells-fargo",
    "chase": "chase",
    "discover": "discover",
    "bank of america": "bofa",
    "pacific gas and electric": "pge",
    "pg&e": "pge",
    "at&t": "att",
    "waste connections": "wci",
}


def resolve_issuer_slug(name: str) -> str:
    """Map issuer display name to canonical entity slug.

    Checks ISSUER_SLUGS first, then falls back to a generated slug
    (lowercase, hyphenated, common suffixes stripped).
    """
    lower = name.lower().strip()
    for pattern, slug in ISSUER_SLUGS.items():
        if pattern in lower:
            return slug
    slug = re.sub(r"[^a-z0-9]+", "-", lower).strip("-")
    for suffix in ("bank", "inc", "corp", "na", "llc"):
        slug = re.sub(rf"-{suffix}$", "", slug)
    return slug


def extract_issuer_name(parsed: dict[str, Any], statement_type: str) -> str:
    """Get the issuer/bank/provider display name from parsed data."""
    if statement_type in ("credit_card", "ploc"):
        return parsed.get("issuer", "")
    if statement_type == "checking":
        return parsed.get("bank", "")
    return parsed.get("provider", "")


def extract_account_suffix(parsed: dict[str, Any], statement_type: str) -> str:
    """Get a short account identifier (last 4 digits or equivalent)."""
    if statement_type in ("credit_card", "checking"):
        return parsed.get("account_last4", "")
    num = parsed.get("account_number", "")
    return num[-4:] if len(num) >= 4 else num


def extract_period(parsed: dict[str, Any], statement_type: str) -> tuple[str, str]:
    """Get (start_date, end_date) from parsed statement data."""
    if statement_type in ("utility", "phone"):
        period = parsed.get("billing_period", {})
    elif statement_type == "ploc":
        sd = parsed.get("statement_date", "")
        return (sd, sd)
    else:
        period = parsed.get("statement_period", {})
    return (period.get("start", ""), period.get("end", ""))


def _add_days(iso_date: str, days: int) -> str | None:
    """Add *days* to an ISO date string. Returns None on parse failure."""
    try:
        return (date.fromisoformat(iso_date) + timedelta(days=days)).isoformat()
    except (ValueError, TypeError):
        return None


def _fmt(val: float | int | None) -> str:
    """Format a numeric value as currency string."""
    if val is None:
        return "$?"
    return f"${val:,.2f}"
