"""Statement type schema definitions — target JSON shapes for LLM extraction.

Each key maps a statement_type to the expected JSON output shape that Claude
should produce when parsing that type of financial document.
"""

from __future__ import annotations

from typing import Any

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
    "mortgage": {
        "servicer": "string",
        "account_number": "string",
        "property_address": "string",
        "statement_date": "YYYY-MM-DD",
        "loan_number": "string",
        "original_loan_amount": 0.0,
        "original_loan_date": "YYYY-MM-DD",
        "interest_rate": 0.0,
        "rate_type": "fixed|adjustable",
        "loan_term_months": 360,
        "maturity_date": "YYYY-MM-DD",
        "principal_balance": 0.0,
        "escrow_balance": 0.0,
        "monthly_payment": 0.0,
        "payment_breakdown": {
            "principal": 0.0,
            "interest": 0.0,
            "escrow": 0.0,
            "other": 0.0,
        },
        "next_due_date": "YYYY-MM-DD",
        "past_due_amount": 0.0,
        "ytd_principal_paid": 0.0,
        "ytd_interest_paid": 0.0,
        "ytd_taxes_paid": 0.0,
        "ytd_insurance_paid": 0.0,
        "transactions": [
            {
                "date": "YYYY-MM-DD",
                "description": "string",
                "amount": 0.0,
                "type": "payment|principal|interest|escrow|fee|disbursement",
            }
        ],
    },
    "escrow": {
        "servicer": "string",
        "account_number": "string",
        "loan_number": "string",
        "statement_date": "YYYY-MM-DD",
        "analysis_period": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
        "current_balance": 0.0,
        "required_minimum": 0.0,
        "projected_low_point": 0.0,
        "monthly_escrow_payment": 0.0,
        "previous_monthly_payment": 0.0,
        "new_monthly_payment": 0.0,
        "shortage_amount": 0.0,
        "surplus_amount": 0.0,
        "disbursements": [
            {
                "type": "property_tax|homeowners_insurance|pmi|flood_insurance|hoa|other",
                "payee": "string",
                "amount": 0.0,
                "date": "YYYY-MM-DD",
            }
        ],
        "projected_disbursements": [
            {
                "type": "property_tax|homeowners_insurance|pmi|flood_insurance|hoa|other",
                "payee": "string",
                "amount": 0.0,
                "expected_date": "YYYY-MM-DD",
            }
        ],
    },
}
