"""Statement type schemas and ingestion helpers for the finance pipeline.

Phase 2: schema dicts describing expected JSON shape per statement type.
Phase 3: issuer slug resolution and assertion builders for Cortex ingestion.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Any

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
    }
)

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
    "student_loan": {
        "servicer": "string",
        "account_number": "string",
        "statement_date": "YYYY-MM-DD",
        "loans": [
            {
                "loan_type": "subsidized|unsubsidized|graduate_plus|parent_plus|private|consolidated",
                "original_principal": 0.0,
                "current_principal": 0.0,
                "accrued_interest": 0.0,
                "interest_rate": 0.0,
                "status": "repayment|deferment|forbearance|grace|default",
                "group_name": "string",
            }
        ],
        "total_balance": 0.0,
        "monthly_payment": 0.0,
        "next_due_date": "YYYY-MM-DD",
        "repayment_plan": "standard|graduated|extended|income_driven|SAVE|PAYE|IBR|ICR",
        "payments_made_ytd": 0.0,
        "interest_paid_ytd": 0.0,
    },
    "brokerage": {
        "broker": "string",
        "account_number": "string",
        "account_type": "individual|joint|ira_traditional|ira_roth|401k|rollover_ira|sep_ira",
        "statement_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "beginning_value": 0.0,
        "ending_value": 0.0,
        "net_deposits_withdrawals": 0.0,
        "investment_return": 0.0,
        "fees_total": 0.0,
        "dividends_total": 0.0,
        "interest_income": 0.0,
        "realized_gains": 0.0,
        "cash_balance": 0.0,
        "margin_balance": 0.0,
        "holdings": [
            {
                "symbol": "string",
                "name": "string",
                "quantity": 0.0,
                "price": 0.0,
                "market_value": 0.0,
                "cost_basis": 0.0,
                "unrealized_gain": 0.0,
                "asset_class": "equity|fixed_income|options|etf|mutual_fund|cash|crypto|other",
            }
        ],
        "transactions": [
            {
                "date": "YYYY-MM-DD",
                "description": "string",
                "symbol": "string",
                "type": "buy|sell|dividend|interest|fee|transfer_in|transfer_out",
                "quantity": 0.0,
                "price": 0.0,
                "amount": 0.0,
            }
        ],
    },
    "tax_document": {
        "form_type": "1099-INT|1099-DIV|1099-B|1099-MISC|1099-NEC|W-2|1098|1095-A|other",
        "tax_year": 2025,
        "issuer": "string",
        "recipient": "string",
        "recipient_tin_last4": "string",
        "filing_date": "YYYY-MM-DD",
        "amounts": {"box_1": {"label": "string", "amount": 0.0}},
        "summary_total": 0.0,
    },
    "property_tax": {
        "authority": "string",
        "parcel_number": "string",
        "property_address": "string",
        "tax_year": "2025-2026",
        "assessed_value": {"land": 0.0, "improvements": 0.0, "total": 0.0},
        "exemptions": [{"type": "string", "amount": 0.0}],
        "installments": [
            {
                "number": 1,
                "amount": 0.0,
                "due_date": "YYYY-MM-DD",
                "status": "paid|due|delinquent|pending",
                "paid_date": None,
            }
        ],
        "total_tax": 0.0,
        "total_due": 0.0,
        "special_assessments": [{"description": "string", "amount": 0.0}],
        "tax_rate_area": "string",
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
    # Student loan servicers
    "nelnet": "nelnet",
    "mohela": "mohela",
    "aidvantage": "aidvantage",
    "great lakes": "great-lakes",
    "navient": "navient",
    "fedloan": "fedloan",
    "sofi": "sofi",
    "earnest": "earnest",
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
    if statement_type == "student_loan":
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
    num = parsed.get("account_number", "")
    return num[-4:] if len(num) >= 4 else num


def extract_period(parsed: dict[str, Any], statement_type: str) -> tuple[str, str]:
    """Get (start_date, end_date) from parsed statement data."""
    if statement_type in ("utility", "phone"):
        period = parsed.get("billing_period", {})
    elif statement_type in ("ploc", "student_loan"):
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
