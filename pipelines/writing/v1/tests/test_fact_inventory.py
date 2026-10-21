"""Fact-inventory intake, assemble drops, drift_check, and finalize status."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from writing_intake.fact_inventory import build_fact_inventory, run_drift_check
from writing_intake.schema import validate_intake_payload

_PKG = "writing_v1_handlers"
if _PKG not in sys.modules:
    _HANDLERS = Path(__file__).resolve().parents[1] / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

assemble_mod = importlib.import_module("writing_v1_handlers.assemble")
finalize_mod = importlib.import_module("writing_v1_handlers.finalize")

pytestmark = pytest.mark.offline

_AAB_PRIOR = """
I declare under penalty of perjury that the following is true.
The stamp reads 'RECEIVED APR 15 2026' which the Clerk stamped that day.
Tenant received the June 5, 2026 notice (Exhibit 2).
Landlord billed on bill-created date of March 6, 2026.
That was 48 days after the June 5, 2026 letter.
Paragraphs 2 through 5 above set out the timeline.
Rent was unpaid for April 2026 (Exhibit 3).
Notice complied with Civil Code section 798.56 (Exhibit 4).
Hearing was scheduled for July 2026 (Exhibit 5).
Service matched the lease addendum (Exhibit 6).
Tenant appeared at the side office in the building orally or in writing.
MANSUBI KAYWAN ETAL / MANSUBI, SHERWIN were listed on the envelope it came in.
rev 5 — not legal advice — draft.
At 5:10 PM that day, I emailed her a list of repairs (Exhibit 7).
The reinstatement request was filed May 27, 2026 (Exhibit 8).
"""


def test_validate_intake_refusal_enrichment() -> None:
    _intake, refusal = validate_intake_payload({"messages": [{"role": "user", "content": "hi"}]})
    assert _intake is None
    assert refusal is not None
    assert "required_fields" in refusal
    assert "minimal_example" in refusal
    assert "fact_inventory" in refusal["minimal_example"]


def test_fact_inventory_validation_requires_prior_draft() -> None:
    _intake, refusal = validate_intake_payload(
        {
            "intake": {
                "mode": "fact_inventory",
                "purpose": "Reinstatement",
                "audience": "ALJ",
            }
        }
    )
    assert _intake is None
    assert refusal is not None
    assert refusal["refused"] == "intake_invalid"


def test_aab_inventory_drops_recitals_keeps_exhibits() -> None:
    inv = build_fact_inventory(
        _AAB_PRIOR,
        purpose="AAB reinstatement declaration",
        audience="Administrative law judge",
    )
    reasons = {row["reason"] for row in inv["dropped_facts"]}
    assert "clerical_intake_stamp" in reasons
    assert "day_count_arithmetic" in reasons
    assert "self_referential_pointer" in reasons
    assert "process_chat_residue" in reasons
    joined = " ".join(inv["load_bearing_claims"])
    for n in (2, 3, 4, 5, 6, 8):
        assert f"Exhibit {n}" in joined
    assert not any("Exhibit 7" in row for row in inv["load_bearing_claims"])


def test_drift_check_flags_bad_draft() -> None:
    bad = (
        "Paragraphs 2 through 5 above set out the facts. "
        "The stamp reads RECEIVED APR 15 2026. "
        "bill-created date of March 6, 2026. "
        "48 days after the June 5 letter."
    )
    out = run_drift_check(bad, channel="filing", pin_ledger=[])
    assert out["passed"] is False
    assert out["flagged_sentences"]


class _Out:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.json = payload
        self.error = None


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs
        self.options = {}


def test_finalize_status_refused_and_degraded() -> None:
    refused = asyncio.run(
        finalize_mod.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {
                    "assemble": _Out(
                        {"refused": "intake_invalid", "error": "bad intake"}
                    )
                }
            ),
        )
    ).json
    assert refused["status"] == "refused"
    assert refused["error"]

    degraded = asyncio.run(
        finalize_mod.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "ok": True,
                            "refused": None,
                            "pin_ledger": [],
                            "writer_seat": "local",
                            "reviewer_seat": "local",
                            "output": "envelope",
                        }
                    ),
                    "draft": _Out({"draft": "Body", "claims": []}),
                    "independence": _Out({"ok": True}),
                    "fact_ceiling_audit": _Out(
                        {"verdict": "revise", "findings": [], "status": "ok"}
                    ),
                    "provenance_check": _Out(
                        {
                            "violations": [{"type": "x"}],
                            "pass": False,
                            "added_facts": [],
                            "drift_check": {"passed": True, "flagged_sentences": []},
                        }
                    ),
                }
            ),
        )
    ).json
    assert degraded["ship_gate"]["pass"] is False
    assert degraded["status"] == "degraded"
    assert degraded.get("error")
