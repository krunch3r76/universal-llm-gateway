"""Phase 4 assertion builders: student_loan, brokerage, tax_document, property_tax.

Split from _finance_assertions.py for SLOC compliance. Same interface:
each builder takes a parsed dict and returns assertion template dicts.
"""

from __future__ import annotations

from typing import Any

from ._finance_schemas import _add_days, _fmt


def _build_student_loan_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    sd = p.get("statement_date", "")
    dd = p.get("next_due_date")
    next_stmt = _add_days(sd, 35) if sd else None
    out: list[dict[str, str | None]] = []
    if p.get("total_balance") is not None and sd:
        out.append(
            {
                "claim": f"Total student loan balance is {_fmt(p['total_balance'])} as of {sd}",
                "valid_from": sd,
                "valid_until": next_stmt,
            }
        )
    if p.get("monthly_payment") is not None and dd:
        out.append(
            {
                "claim": f"Monthly payment {_fmt(p['monthly_payment'])} due {dd}",
                "valid_from": sd or None,
                "valid_until": dd,
            }
        )
    for loan in p.get("loans", []):
        ltype = loan.get("loan_type", "unknown")
        principal = loan.get("current_principal")
        rate = loan.get("interest_rate")
        if principal is not None and rate is not None:
            out.append(
                {
                    "claim": f"{ltype} loan: principal {_fmt(principal)} at {rate}%",
                    "valid_from": sd or None,
                    "valid_until": next_stmt,
                }
            )
    if p.get("repayment_plan"):
        out.append(
            {
                "claim": f"Repayment plan: {p['repayment_plan']}",
                "valid_from": sd or None,
                "valid_until": None,
            }
        )
    if p.get("interest_paid_ytd") is not None and sd:
        year = sd[:4]
        out.append(
            {
                "claim": f"YTD interest paid: {_fmt(p['interest_paid_ytd'])}",
                "valid_from": sd,
                "valid_until": f"{year}-12-31" if year else None,
            }
        )
    return out


def _build_brokerage_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    period = p.get("statement_period", {})
    start, end = period.get("start", ""), period.get("end", "")
    next_stmt = _add_days(end, 35) if end else None
    out: list[dict[str, str | None]] = []
    if p.get("ending_value") is not None and end:
        out.append(
            {
                "claim": f"Account value {_fmt(p['ending_value'])} as of {end}",
                "valid_from": end,
                "valid_until": next_stmt,
            }
        )
    if p.get("cash_balance") is not None and end:
        out.append(
            {
                "claim": f"Cash balance {_fmt(p['cash_balance'])} as of {end}",
                "valid_from": end,
                "valid_until": next_stmt,
            }
        )
    if p.get("investment_return") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Investment return {_fmt(p['investment_return'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    if p.get("fees_total") is not None and start and end:
        out.append(
            {
                "claim": f"Total fees {_fmt(p['fees_total'])} for period {start}\u2013{end}",
                "valid_from": start,
                "valid_until": end,
            }
        )
    if p.get("dividends_total") is not None and start and end:
        out.append(
            {
                "claim": (
                    f"Dividends received {_fmt(p['dividends_total'])} "
                    f"for period {start}\u2013{end}"
                ),
                "valid_from": start,
                "valid_until": end,
            }
        )
    if p.get("margin_balance") and end:
        out.append(
            {
                "claim": f"Margin balance {_fmt(p['margin_balance'])} as of {end}",
                "valid_from": end,
                "valid_until": next_stmt,
            }
        )
    return out


def _build_tax_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    year = str(p.get("tax_year", ""))
    form = p.get("form_type", "")
    issuer = p.get("issuer", "unknown")
    yr_start = f"{year}-01-01" if year else None
    yr_end = f"{year}-12-31" if year else None
    out: list[dict[str, str | None]] = []
    if p.get("summary_total") is not None and form:
        out.append(
            {
                "claim": (
                    f"{form} from {issuer} for tax year {year}: "
                    f"total {_fmt(p['summary_total'])}"
                ),
                "valid_from": yr_start,
                "valid_until": yr_end,
            }
        )
    for box_key, box_val in (p.get("amounts") or {}).items():
        if isinstance(box_val, dict) and box_val.get("amount"):
            label = box_val.get("label", box_key)
            out.append(
                {
                    "claim": f"{form} {box_key} ({label}): {_fmt(box_val['amount'])}",
                    "valid_from": yr_start,
                    "valid_until": yr_end,
                }
            )
    return out


def _build_mortgage_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    sd = p.get("statement_date", "")
    dd = p.get("next_due_date")
    next_stmt = _add_days(sd, 35) if sd else None
    out: list[dict[str, str | None]] = []
    if p.get("principal_balance") is not None and sd:
        out.append(
            {
                "claim": f"Principal balance is {_fmt(p['principal_balance'])} as of {sd}",
                "valid_from": sd,
                "valid_until": next_stmt,
            }
        )
    if p.get("monthly_payment") is not None and dd:
        out.append(
            {
                "claim": f"Monthly payment {_fmt(p['monthly_payment'])} due {dd}",
                "valid_from": sd or None,
                "valid_until": dd,
            }
        )
    breakdown = p.get("payment_breakdown", {})
    if breakdown and sd:
        parts = []
        for k in ("principal", "interest", "escrow", "other"):
            v = breakdown.get(k)
            if v:
                parts.append(f"{k} {_fmt(v)}")
        if parts:
            out.append(
                {
                    "claim": f"Payment breakdown: {', '.join(parts)}",
                    "valid_from": sd,
                    "valid_until": next_stmt,
                }
            )
    if p.get("interest_rate") is not None:
        rate_type = p.get("rate_type", "fixed")
        out.append(
            {
                "claim": f"Interest rate {p['interest_rate']}% ({rate_type})",
                "valid_from": sd or None,
                "valid_until": None,
            }
        )
    if p.get("escrow_balance") is not None and sd:
        out.append(
            {
                "claim": f"Escrow balance {_fmt(p['escrow_balance'])} as of {sd}",
                "valid_from": sd,
                "valid_until": next_stmt,
            }
        )
    if p.get("past_due_amount") and sd:
        out.append(
            {
                "claim": f"Past due amount: {_fmt(p['past_due_amount'])}",
                "valid_from": sd,
                "valid_until": next_stmt,
            }
        )
    if p.get("ytd_interest_paid") is not None and sd:
        year = sd[:4]
        out.append(
            {
                "claim": f"YTD interest paid: {_fmt(p['ytd_interest_paid'])}",
                "valid_from": sd,
                "valid_until": f"{year}-12-31" if year else None,
            }
        )
    return out


def _build_escrow_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    sd = p.get("statement_date", "")
    period = p.get("analysis_period", {})
    start = period.get("start", sd)
    end = period.get("end", sd)
    next_stmt = _add_days(end, 35) if end else None
    out: list[dict[str, str | None]] = []
    if p.get("current_balance") is not None and sd:
        out.append(
            {
                "claim": f"Escrow balance {_fmt(p['current_balance'])} as of {sd}",
                "valid_from": sd,
                "valid_until": next_stmt,
            }
        )
    if p.get("monthly_escrow_payment") is not None:
        out.append(
            {
                "claim": f"Monthly escrow payment: {_fmt(p['monthly_escrow_payment'])}",
                "valid_from": start or None,
                "valid_until": end or None,
            }
        )
    if (
        p.get("new_monthly_payment") is not None
        and p.get("previous_monthly_payment") is not None
    ):
        prev = _fmt(p["previous_monthly_payment"])
        new = _fmt(p["new_monthly_payment"])
        out.append(
            {
                "claim": f"Total monthly payment changing from {prev} to {new}",
                "valid_from": end or None,
                "valid_until": None,
            }
        )
    if p.get("shortage_amount") and p["shortage_amount"] > 0:
        out.append(
            {
                "claim": f"Escrow shortage: {_fmt(p['shortage_amount'])}",
                "valid_from": sd or None,
                "valid_until": end or None,
            }
        )
    if p.get("surplus_amount") and p["surplus_amount"] > 0:
        out.append(
            {
                "claim": f"Escrow surplus: {_fmt(p['surplus_amount'])}",
                "valid_from": sd or None,
                "valid_until": end or None,
            }
        )
    for disb in p.get("disbursements", []):
        dtype = disb.get("type", "other").replace("_", " ")
        payee = disb.get("payee", "")
        amt = disb.get("amount")
        ddate = disb.get("date")
        if amt is not None and ddate:
            label = f"{dtype} to {payee}" if payee else dtype
            out.append(
                {
                    "claim": f"Escrow disbursement: {label} {_fmt(amt)} on {ddate}",
                    "valid_from": ddate,
                    "valid_until": ddate,
                }
            )
    return out


def _build_property_tax_assertions(p: dict[str, Any]) -> list[dict[str, str | None]]:
    ty = p.get("tax_year", "")
    out: list[dict[str, str | None]] = []
    if p.get("total_tax") is not None:
        out.append(
            {
                "claim": f"Property tax total {_fmt(p['total_tax'])} for {ty}",
                "valid_from": str(ty) if ty else None,
                "valid_until": str(ty) if ty else None,
            }
        )
    for inst in p.get("installments", []):
        num = inst.get("number", "?")
        amt = inst.get("amount")
        dd = inst.get("due_date")
        status = inst.get("status", "unknown")
        if amt is not None and dd:
            out.append(
                {
                    "claim": f"Installment {num}: {_fmt(amt)} due {dd} \u2014 {status}",
                    "valid_from": str(ty) if ty else None,
                    "valid_until": dd,
                }
            )
    av = p.get("assessed_value", {})
    if av.get("total") is not None:
        out.append(
            {
                "claim": (
                    f"Assessed value: land {_fmt(av.get('land'))}, "
                    f"improvements {_fmt(av.get('improvements'))}, "
                    f"total {_fmt(av['total'])}"
                ),
                "valid_from": str(ty) if ty else None,
                "valid_until": str(ty) if ty else None,
            }
        )
    return out
