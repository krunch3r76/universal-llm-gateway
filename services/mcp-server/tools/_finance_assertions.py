"""Per-statement-type assertion builders for Cortex ingestion.

Each builder takes a parsed dict and returns a list of assertion templates
with claim, valid_from, and valid_until fields.
"""

from __future__ import annotations

from typing import Any

from ._finance_assertions_ext import (
    _build_brokerage_assertions,
    _build_escrow_assertions,
    _build_mortgage_assertions,
    _build_property_tax_assertions,
    _build_student_loan_assertions,
    _build_tax_assertions,
)
from ._finance_schemas import _add_days, _fmt, extract_period


def build_assertions(
    parsed: dict[str, Any], statement_type: str
) -> list[dict[str, str | None]]:
    """Build temporally scoped assertion dicts for a parsed statement.

    Returns a list of dicts with keys: claim, valid_from, valid_until.
    """
    builders = {
        "credit_card": _build_cc_assertions,
        "checking": _build_checking_assertions,
        "utility": _build_utility_assertions,
        "phone": _build_phone_assertions,
        "ploc": _build_ploc_assertions,
        "student_loan": _build_student_loan_assertions,
        "brokerage": _build_brokerage_assertions,
        "tax_document": _build_tax_assertions,
        "property_tax": _build_property_tax_assertions,
        "mortgage": _build_mortgage_assertions,
        "escrow": _build_escrow_assertions,
    }
    builder = builders.get(statement_type)
    return builder(parsed) if builder else []


def _build_cc_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    start, end = extract_period(p, "credit_card")
    sd = p.get("statement_date") or end
    dd = p.get("due_date")
    next_stmt = _add_days(sd, 35) if sd else None
    out: list[dict[str, str | None]] = []
    if p.get("new_balance") is not None:
        out.append(
            {
                "claim": f"Balance is {_fmt(p['new_balance'])} as of {sd}",
                "valid_from": sd,
                "valid_until": next_stmt,
            }
        )
    if p.get("minimum_due") is not None and dd:
        out.append(
            {
                "claim": f"Minimum payment {_fmt(p['minimum_due'])} due {dd}",
                "valid_from": sd,
                "valid_until": dd,
            }
        )
    for rate in p.get("interest_rates", []):
        if rate.get("apr") is not None:
            label = rate.get("type", "purchase").replace("_", " ").title()
            out.append(
                {
                    "claim": f"{label} APR is {rate['apr']}%",
                    "valid_from": start or None,
                    "valid_until": None,
                }
            )
    if p.get("credit_limit") is not None:
        out.append(
            {
                "claim": f"Credit limit is {_fmt(p['credit_limit'])}",
                "valid_from": None,
                "valid_until": None,
            }
        )
    if p.get("interest_total") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Interest charged {_fmt(p['interest_total'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    if p.get("payments_total") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Payments totaling {_fmt(p['payments_total'])} "
                    f"applied in period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    return out


def _build_checking_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    start, end = extract_period(p, "checking")
    out: list[dict[str, str | None]] = []
    if p.get("closing_balance") is not None and end:
        out.append(
            {
                "claim": f"Closing balance {_fmt(p['closing_balance'])} as of {end}",
                "valid_from": end,
                "valid_until": _add_days(end, 35),
            }
        )
    if p.get("deposits_total") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Total deposits {_fmt(p['deposits_total'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    if p.get("withdrawals_total") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Total withdrawals {_fmt(p['withdrawals_total'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    if p.get("fees_total") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Fees charged {_fmt(p['fees_total'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    return out


def _build_utility_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    start, end = extract_period(p, "utility")
    dd = p.get("due_date")
    out: list[dict[str, str | None]] = []
    if p.get("amount_due") is not None and dd:
        out.append(
            {
                "claim": f"Amount due {_fmt(p['amount_due'])} by {dd}",
                "valid_from": end or None,
                "valid_until": dd,
            }
        )
    if p.get("current_charges") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Current charges {_fmt(p['current_charges'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    usage = p.get("usage", {})
    if usage.get("electric_kwh") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Electric usage {usage['electric_kwh']} kWh "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    if usage.get("gas_therms") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Gas usage {usage['gas_therms']} therms "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    return out


def _build_phone_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    start, end = extract_period(p, "phone")
    dd = p.get("due_date")
    out: list[dict[str, str | None]] = []
    if p.get("amount_due") is not None and dd:
        out.append(
            {
                "claim": f"Amount due {_fmt(p['amount_due'])} by {dd}",
                "valid_from": end or None,
                "valid_until": dd,
            }
        )
    if p.get("current_charges") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Current charges {_fmt(p['current_charges'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    for line in p.get("lines", []):
        num = line.get("number", "?")
        charges = line.get("charges")
        if charges is not None:
            out.append(
                {
                    "claim": (
                        f"Line {num} charges {_fmt(charges)} "
                        f"for period {start}\u2013{end}"
                    ),
                    "valid_from": start,
                    "valid_until": end,
                }
            )
    return out


def _build_ploc_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    sd = p.get("statement_date", "")
    dd = p.get("due_date")
    next_stmt = _add_days(sd, 35) if sd else None
    out: list[dict[str, str | None]] = []
    if p.get("new_balance") is not None:
        out.append(
            {
                "claim": f"Balance is {_fmt(p['new_balance'])} as of {sd}",
                "valid_from": sd or None,
                "valid_until": next_stmt,
            }
        )
    if p.get("minimum_due") is not None and dd:
        out.append(
            {
                "claim": f"Minimum payment {_fmt(p['minimum_due'])} due {dd}",
                "valid_from": sd or None,
                "valid_until": dd,
            }
        )
    for rate in p.get("interest_rates", []):
        if rate.get("apr") is not None:
            out.append(
                {
                    "claim": f"APR is {rate['apr']}%",
                    "valid_from": sd or None,
                    "valid_until": None,
                }
            )
    if p.get("credit_limit") is not None:
        out.append(
            {
                "claim": f"Credit limit is {_fmt(p['credit_limit'])}",
                "valid_from": None,
                "valid_until": None,
            }
        )
    if p.get("interest_total") is not None and sd:
        out.append(
            {
                "claim": (
                    f"Interest charged {_fmt(p['interest_total'])} "
                    f"for period ending {sd}"
                ),
                "valid_from": sd,
                "valid_until": sd,
            }
        )
    return out
