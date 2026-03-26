"""Statement type schemas and ingestion helpers for the finance pipeline.

Phase 2: schema dicts describing expected JSON shape per statement type.
Phase 3: issuer slug resolution and assertion builders for Cortex ingestion.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

from ._finance_schema_defs import (
    STATEMENT_SCHEMAS as STATEMENT_SCHEMAS,  # noqa: PLC0414
)

VALID_TYPES = frozenset(
    {
        "checking",
        "credit_card",
        "utility",
        "phone",
        "ploc",
        "student_loan",
        "brokerage",
        "tax_document",
        "property_tax",
        "mortgage",
        "escrow",
    }
)


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
    # Student loan servicers
    "nelnet": "nelnet",
    "mohela": "mohela",
    "aidvantage": "aidvantage",
    "great lakes": "great-lakes",
    "navient": "navient",
    "fedloan": "fedloan",
    "sofi": "sofi",
    "earnest": "earnest",
    # Student loan servicers (cont.)
    "edfinancial": "edfinancial",
    # Mortgage servicers
    "chase home lending": "chase",
    "jpmorgan chase home lending": "chase",
    "wells fargo home mortgage": "wells-fargo",
    "nationstar": "nationstar",
    "mr. cooper": "mr-cooper",
    "loancare": "loancare",
    "newrez": "newrez",
    "pennymac": "pennymac",
    "freedom mortgage": "freedom-mortgage",
    # Brokerages
    "charles schwab": "schwab",
    "schwab": "schwab",
    "fidelity": "fidelity",
    "vanguard": "vanguard",
    "td ameritrade": "td-ameritrade",
    "e*trade": "etrade",
    "robinhood": "robinhood",
    "interactive brokers": "ibkr",
    "merrill": "merrill",
    "coinbase": "coinbase",
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
    if statement_type in ("credit_card", "ploc", "tax_document"):
        return parsed.get("issuer", "")
    if statement_type == "checking":
        return parsed.get("bank", "")
    if statement_type in ("student_loan", "mortgage", "escrow"):
        return parsed.get("servicer", "")
    if statement_type == "brokerage":
        return parsed.get("broker", "")
    if statement_type == "property_tax":
        return parsed.get("authority", "")
    return parsed.get("provider", "")


def extract_account_suffix(parsed: dict[str, Any], statement_type: str) -> str:
    """Get a short account identifier (last 4 digits or equivalent)."""
    if statement_type in ("credit_card", "checking"):
        return parsed.get("account_last4", "")
    if statement_type == "tax_document":
        return parsed.get("recipient_tin_last4", "")
    if statement_type == "property_tax":
        return parsed.get("parcel_number", "")
    if statement_type in ("mortgage", "escrow"):
        loan = parsed.get("loan_number", "")
        if len(loan) >= 4:
            return loan[-4:]
        num = parsed.get("account_number", "")
        return num[-4:] if len(num) >= 4 else num
    num = parsed.get("account_number", "")
    return num[-4:] if len(num) >= 4 else num


def extract_period(parsed: dict[str, Any], statement_type: str) -> tuple[str, str]:
    """Get (start_date, end_date) from parsed statement data."""
    if statement_type in ("utility", "phone"):
        period = parsed.get("billing_period", {})
    elif statement_type in ("ploc", "student_loan", "mortgage"):
        sd = parsed.get("statement_date", "")
        return (sd, sd)
    elif statement_type == "escrow":
        period = parsed.get("analysis_period", {})
        if period:
            return (period.get("start", ""), period.get("end", ""))
        sd = parsed.get("statement_date", "")
        return (sd, sd)
    elif statement_type == "tax_document":
        year = str(parsed.get("tax_year", ""))
        return (f"{year}-01-01", f"{year}-12-31") if year else ("", "")
    elif statement_type == "property_tax":
        ty = parsed.get("tax_year", "")
        return (str(ty), str(ty))
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
