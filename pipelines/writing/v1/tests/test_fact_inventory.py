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
from writing_intake.schema import OutboundWriteIntake, validate_intake_payload

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


def test_outbound_write_intake_accepts_recipient_role() -> None:
    intake, refusal = validate_intake_payload(
        {
            "intake": {
                "verbatim_notes": ["Decline standing availability."],
                "recipient": "Maria",
                "objective": "Reply to VMS",
                "channel": "email",
                "recipient_role": "VMS scheduler",
            }
        }
    )
    assert refusal is None
    assert intake is not None
    assert intake.recipient_role == "VMS scheduler"
    model = OutboundWriteIntake.model_validate(
        {
            "verbatim_notes": ["Note."],
            "recipient": "Clerk",
            "objective": "Notify",
            "channel": "sms",
            "recipient_role": "  intake coordinator  ",
        }
    )
    assert model.recipient_role == "intake coordinator"


def test_validate_intake_refusal_enrichment() -> None:
    _intake, refusal = validate_intake_payload(
        {"messages": [{"role": "user", "content": "hi"}]}
    )
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


_AAB_PURPOSE = (
    "Support July 23, 2026 Requests for Reinstatement (Applications 25.7489 and 25.7490) "
    "regarding timeliness: conflicting Dec 16, 2025 Prop 19 and Jan 16, 2026 supplemental "
    "notices; unreturned calls to Deborah Bathurst during undisclosed emergency leave; "
    "March 25, 2026 Chase escrow discovery and April 15, 2026 Assessor visit explaining "
    "first knowledge of issue; May 27, 2026 in-person filing, June 5, 2026 closure notice, "
    "and timely July 23, 2026 reinstatement within 60 days."
)


def test_aab_inventory_drops_recitals_keeps_exhibits() -> None:
    inv = build_fact_inventory(
        _AAB_PRIOR,
        purpose=_AAB_PURPOSE,
        audience="Administrative law judge",
    )
    reasons = {row["reason"] for row in inv["dropped_facts"]}
    assert "clerical_intake_stamp" in reasons
    assert "day_count_arithmetic" in reasons
    assert "self_referential_pointer" in reasons
    assert "process_chat_residue" in reasons
    joined = " ".join(inv["load_bearing_claims"])
    for n in (2, 3, 4, 5, 8):
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

    clean_numbered = (
        "I, Kaywan Joseph Mansubi, state:\n\n"
        "1. The Assessor's Notice of Supplemental Assessment dated January 16, 2026 showed a "
        "reappraised value of $2,800,000 (Exhibit 2).\n\n"
        "2. Rafael Aranda called me into a side office and told me Ms. Bathurst had been on emergency leave.\n\n"
        "3. On May 27, 2026, I delivered my Assessment Appeal Applications in person to Dr. Amber Green. "
        "On May 28, 2026, she emailed me confirming submission (Exhibit 7.).\n"
    )
    inv = build_fact_inventory(
        clean_numbered,
        purpose=_AAB_PURPOSE,
        audience="Assessment Appeals Board",
    )
    assert len(inv["load_bearing_claims"]) == 4
    assert inv["sentence_count"] == 4
    assert inv["input_sha256"]
    assert inv["checked_sha256"]
    assert inv["sworn_body_word_count"] > inv["checked_word_count"]
    concession = (
        "I received the January 16, 2026 notice at 15571 Flintridge Dr close to its date (Exhibit 2)."
    )
    inv_bad = build_fact_inventory(
        concession,
        purpose="Timeliness hearing",
        audience="Assessment Appeals Board",
    )
    assert any(
        row.get("reason") == "volunteered_concession" for row in inv_bad["dropped_facts"]
    )
    drift_clean = run_drift_check(
        clean_numbered,
        channel="filing",
        pin_ledger=[{"id": "P1", "excerpt": "Exhibit 2, Exhibit 4, Exhibit 7"}],
    )
    assert drift_clean["passed"] is True
    assert drift_clean["input_sha256"] == inv["input_sha256"]
    assert drift_clean["checked_sha256"] == inv["checked_sha256"]
    assert drift_clean["sworn_body_word_count"] == inv["sworn_body_word_count"]
    assert drift_clean["checked_word_count"] == inv["checked_word_count"]
    assert drift_clean["sentence_count"] == 4
    assert drift_clean["check_profile"] == "sworn"
    assert drift_clean["flagged_sentences"] == []


class _Out:
    def __init__(
        self,
        payload: dict[str, Any] | None,
        *,
        error: str | None = None,
        raw: str = "",
        model_id: str | None = None,
    ) -> None:
        self.json = payload
        self.error = error
        self.raw = raw
        self.model_id = model_id
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.model_call_count = 0


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs
        self.options = {}


def test_finalize_status_refused_and_degraded() -> None:
    refused = asyncio.run(
        finalize_mod.WritingFinalizeHandler().execute(
            None,
            _Ctx(
                {"assemble": _Out({"refused": "intake_invalid", "error": "bad intake"})}
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


_HERMES = "hermes-3-llama-3-1-70b-uncensored-q4-k-m-32768-hybrid"
_QWEN = "qwen3-14b-q4-k-m-40960"
_JURAT = (
    "I declare under penalty of perjury under the laws of the State of "
    "California that the foregoing is true and correct."
)
_CAPTION = "BOARD OF EXAMPLE"


class _IntakeCtx:
    def __init__(
        self,
        options: dict[str, Any],
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        self.options = options
        self.messages = messages


def _gate_outputs(
    *,
    body: str,
    drift: dict[str, Any],
    v2_raw: str | None = None,
    v2_drift: dict[str, Any] | None = None,
    v2_error: str | None = None,
    channel: str | None = None,
    filing_frame: dict[str, str] | None = None,
) -> dict[str, Any]:
    assemble_payload: dict[str, Any] = {
        "ok": True,
        "refused": None,
        "pin_ledger": [],
        "writer_seat": "local",
        "reviewer_seat": "local",
        "output": "envelope",
    }
    if channel:
        assemble_payload["channel"] = channel
    if filing_frame is not None:
        assemble_payload["filing_frame"] = filing_frame
    outputs: dict[str, Any] = {
        "assemble": _Out(assemble_payload),
        "draft": _Out({"draft": body, "claims": []}, model_id=_HERMES),
        "independence": _Out({"ok": True, "refused": None}),
        "fact_ceiling_audit": _Out(
            {"verdict": "ship", "findings": [], "status": "ok"},
            model_id=_QWEN,
        ),
        "provenance_check": _Out(
            {
                "violations": [],
                "pass": True,
                "added_facts": [],
                "drift_check": drift,
            }
        ),
    }
    if v2_raw is not None or v2_error is not None:
        outputs["prose_rhythm_pass"] = _Out(
            None,
            error=v2_error,
            raw=v2_raw or "",
        )
        outputs["fact_ceiling_final"] = _Out(
            {
                "violations": [],
                "pass": True,
                "drift_check": v2_drift or {"passed": True, "flagged_sentences": []},
            }
        )
    return outputs


def _run_finalize(outputs: dict[str, Any]) -> dict[str, Any]:
    payload = asyncio.run(
        finalize_mod.WritingFinalizeHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    return payload


def test_documented_fact_inventory_call_ignores_message_content() -> None:
    prior = (
        f"I, Ada Example, state:\n\nThe rent was unpaid for April 2026.\n\n{_JURAT}\n"
    )
    payload = asyncio.run(
        assemble_mod.WritingAssembleHandler().execute(
            None,
            _IntakeCtx(
                {
                    "intake": {
                        "mode": "fact_inventory",
                        "prior_draft": prior,
                        "purpose": "Hearing",
                        "audience": "Board of Example",
                        "channel": "filing",
                    }
                },
                [{"role": "user", "content": "Write it"}],
            ),
        )
    ).json
    assert payload["ok"] is True
    assert payload["intake"]["prior_draft"] == prior
    assert payload["intake"]["prior_draft"] != "Write it"
    blob = "\n".join(
        str(payload.get(key) or "")
        for key in (
            "packet",
            "verbatim_notes_block",
            "brief_block",
            "documents_block",
        )
    )
    assert "Write it" not in blob


def test_partial_fact_inventory_does_not_adopt_message_as_prior_draft() -> None:
    message = "UNIQUE_MESSAGE_NOT_A_DRAFT"
    intake, refusal = validate_intake_payload(
        {
            "mode": "fact_inventory",
            "purpose": "Reinstatement",
            "audience": "ALJ",
        },
        messages=[{"role": "user", "content": message}],
    )
    assert intake is None
    assert refusal is not None
    assert refusal["refused"] == "intake_invalid"
    assert message not in str(refusal)


def test_inventory_drift_flags_paraphrase_qualifier_and_attribution() -> None:
    claims = [
        "She called a few times a month.",
        "The housing department billed 40 and the board posted the notice.",
        "I stated the rent was unpaid.",
    ]
    paraphrase = (
        "She called monthly. The housing billed and posted the notice. "
        "The rent was unpaid."
    )
    bad = run_drift_check(
        paraphrase,
        channel="email",
        pin_ledger=[],
        load_bearing_claims=claims,
    )
    assert bad["passed"] is False
    flags = [
        row
        for row in bad["flagged_sentences"]
        if row["reason"] == "fact_inventory_drift"
    ]
    missing = {token for row in flags for token in row["missing_tokens"]}
    assert {"few", "times", "month"} <= missing
    assert {"department", "40", "board"} <= missing
    assert "stated" in missing
    good = run_drift_check(
        " ".join(claims),
        channel="email",
        pin_ledger=[],
        load_bearing_claims=claims,
    )
    assert good["passed"] is True
    assert good["flagged_sentences"] == []


def test_revision_delta_flags_pin_keeping_content_drop() -> None:
    provenance = importlib.import_module("writing_v1_handlers.provenance_check")
    v1 = "I stated she called a few times a month."
    v2 = "She called."
    clauses = [{"text": v1, "pin_ids": ["P1"], "disposition": "express"}]
    v2_clauses = [{"text": v2, "pin_ids": ["P1"], "disposition": "express"}]
    delta = provenance.revision_delta(
        {"draft": v1, "claims": clauses},
        {"draft": v2, "claims": v2_clauses},
        v1_clauses=clauses,
        v2_clauses=v2_clauses,
    )
    assert delta != []
    assert any(row["type"] == "fact_dropped" for row in delta)


def test_ship_gate_fails_when_v2_paraphrases_even_if_v1_is_clean() -> None:
    claims = ["I stated she called a few times a month."]
    v1 = claims[0]
    v2 = "She called monthly."
    v1_drift = run_drift_check(
        v1, channel="email", pin_ledger=[], load_bearing_claims=claims
    )
    v2_drift = run_drift_check(
        v2, channel="email", pin_ledger=[], load_bearing_claims=claims
    )
    assert v1_drift["passed"] is True
    assert v2_drift["passed"] is False
    payload = _run_finalize(
        _gate_outputs(body=v1, drift=v1_drift, v2_raw=v2, v2_drift=v2_drift)
    )
    assert payload["provenance"]["drift_check"]["passed"] is False
    assert payload["ship_gate"]["pass"] is False
    assert payload["ship_gate"]["selected"] != "draft_v2"


def test_errored_reviser_is_not_selected() -> None:
    clean = run_drift_check(
        "The rent was unpaid.",
        channel="email",
        pin_ledger=[],
        load_bearing_claims=["The rent was unpaid."],
    )
    payload = _run_finalize(
        _gate_outputs(
            body="The rent was unpaid.",
            drift=clean,
            v2_raw="upstream_transport_error",
            v2_error="upstream_transport_error",
        )
    )
    assert payload["revision"] is None
    assert payload["ship_gate"]["selected"] != "draft_v2"
    assert payload["ship_gate"]["selected"] == "draft_v1"


def test_prose_rhythm_prompt_does_not_strip_trailing_qualifiers() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "prompts" / "prose_rhythm.yaml"
    ).read_text(encoding="utf-8")
    assert "strip trailing qualifiers" not in text
    assert "Orwell" not in text
    assert "pin-backed content word" in text
    assert "I stated" in text
    assert "I said" in text


def test_prose_rhythm_correspondence_keeps_sentence_boundaries() -> None:
    text = (
        Path(__file__).resolve().parents[1] / "prompts" / "prose_rhythm.yaml"
    ).read_text(encoding="utf-8")
    assert "correspondence (email or sms)" in text
    assert "keep separate sentences separate" in text
    assert "four or more" in text
    assert "Currently" in text
    assert "because" in text
    assert "missing_obligations" in text


def test_filing_stitch_keeps_source_frame_and_fails_unnumbered_body() -> None:
    from writing_intake.inventory_drift import filing_frame

    opener = "I, Ada Example, state:"
    claim_a = "The department billed 40 a few times a month."
    claim_b = "I stated the board received notice."
    prior = f"{_CAPTION}\n\n{opener}\n\n{claim_a}\n\n{_JURAT}\n"
    frame = filing_frame(prior)
    assert frame["caption"] == _CAPTION
    assert frame["opener"] == opener
    assert frame["jurat"] == _JURAT
    assembled = asyncio.run(
        assemble_mod.WritingAssembleHandler().execute(
            None,
            _IntakeCtx(
                {
                    "intake": {
                        "mode": "fact_inventory",
                        "prior_draft": prior,
                        "purpose": "Hearing",
                        "audience": "Board of Example",
                        "channel": "filing",
                    }
                }
            ),
        )
    ).json
    assert assembled["filing_frame"]["caption"] == _CAPTION
    assert assembled["filing_frame"]["jurat"] == _JURAT
    claims = [claim_a, claim_b]
    numbered = f"1. {claim_a}\n2. {claim_b}"
    unnumbered = f"{claim_a} {claim_b}"
    numbered_drift = run_drift_check(
        numbered, channel="filing", pin_ledger=[], load_bearing_claims=claims
    )
    unnumbered_drift = run_drift_check(
        unnumbered, channel="filing", pin_ledger=[], load_bearing_claims=claims
    )
    assert numbered_drift["passed"] is True
    assert unnumbered_drift["passed"] is True
    failed = _run_finalize(
        _gate_outputs(
            body=unnumbered,
            drift=unnumbered_drift,
            channel="filing",
            filing_frame=frame,
        )
    )
    assert _CAPTION in failed["filing_text"]
    assert _JURAT in failed["filing_text"]
    assert failed["ship_gate"]["pass"] is False
    assert any(
        row.get("type") == "filing_shape" for row in failed["ship_gate"]["unresolved"]
    )
    passed = _run_finalize(
        _gate_outputs(
            body=numbered,
            drift=numbered_drift,
            channel="filing",
            filing_frame=frame,
        )
    )
    assert passed["ship_gate"]["pass"] is True
    assert _CAPTION in passed["filing_text"]
    assert _JURAT in passed["filing_text"]
    assert passed["filing_text"].index(_CAPTION) < passed["filing_text"].index(_JURAT)
