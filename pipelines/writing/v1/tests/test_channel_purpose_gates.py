"""Channel-aware purpose gates and correspondence drift units."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from writing_intake.claim_gates import map_claim_to_purpose, parse_purpose_points
from writing_intake.fact_inventory import drift_check_sentence, run_drift_check
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

assemble = importlib.import_module("writing_v1_handlers.assemble")
email_envelope = importlib.import_module("writing_v1_handlers.email_envelope")
provenance = importlib.import_module("writing_v1_handlers.provenance_check")
review_schema = importlib.import_module("writing_v1_handlers.review_schema")

pytestmark = pytest.mark.offline

PURPOSE_POINTS = [
    "Exhibits must be submitted electronically at least 3 business days before the hearing to clerk@example.org . "
    "Use the Following File Naming Format: First Name – Last Name – Appeal Number – Document Title – Number",
    "The request for reinstatement of appeal no(s): 99.0001 ... has been scheduled for consideration by the "
    "Assessment Appeals Board on: Date: Tuesday, October 27, 2026 Time: 09:00 AM Location: Remote via Zoom",
    "Before the hearing, provide the Clerk with an email address that can be used to exchange documents during "
    "the hearing, including rebuttal evidence or corrections.",
]
CONFIRM_RECEIPT = "Please confirm receipt of this email and the attachments."

REDRAFT = """To: clerk@example.org
Subject: Exhibits – Reinstatement Hearing Tue 10/27/2026 9:00 AM – Appeal Nos. 99.0001 and 99.0002 – APN 000-00-000

Dear Clerk of the Assessment Appeals Board,

I am the applicant on Assessment Appeal Nos. 99.0001 and 99.0002 (APN 000-00-000, Supplemental Assessment). My requests for reinstatement are scheduled for the Board's hearing on Tuesday, October 27, 2026 at 9:00 AM, remote via Zoom.

My exhibits for that hearing are attached, named in the requested format:

1. Pat – Applicant – 99.0001 and 99.0002 – Declaration re Timeliness – 1
2. Pat – Applicant – 99.0001 and 99.0002 – Notice of Supplemental Assessment dated 2026-01-16 – 2
3. Pat – Applicant – 99.0001 and 99.0002 – Notification of Base Year Value dated 2025-12-16 – 3
4. Pat – Applicant – 99.0001 and 99.0002 – Supplemental Tax Bills 1 and 2 – 4
5. Pat – Applicant – 99.0001 and 99.0002 – Invalid Closed Letters dated 2026-06-05 – 5
6. Pat – Applicant – 99.0001 and 99.0002 – Requests for Reinstatement stamped 2026-07-23 – 6
7. Pat – Applicant – 99.0001 and 99.0002 – Clerk Email of 2026-05-28 Acknowledging Documents Submitted 2026-05-27 – 7
8. Pat – Applicant – 99.0001 and 99.0002 – Letter to Assessor stamped 2026-04-15 – 8

For document exchange during the hearing, please use applicant@example.com.

Thank you,

Pat Applicant
Applicant, Appeal Nos. 99.0001 and 99.0002
[PHONE]
"""

BASELINE = """To: clerk@example.org

Subject: Assessment Appeals 99.0001 and 99.0002, APN 000-00-000, Reinstatement Hearing on October 27, 2026

Hello,

I, Pat Applicant, am submitting my signed CCP § 2015.5 Declaration of Pat Applicant Regarding Timeliness (Exhibit 1) plus Exhibits 2 through 8 for the reinstatement hearing on Assessment Appeals 99.0001 and 99.0002.

The property is at 1 Example St, Townsville, APN 000-00-000. The hearing is set for Tuesday, October 27, 2026, at 9:00 AM PT, on Zoom.

Attached are the following exhibits:

Exhibit 1: Declaration of Pat Applicant Regarding Timeliness (CCP § 2015.5)
Exhibit 2: Notice of Supplemental Assessment dated 2026-01-16

Please confirm receipt of this email and the attachments.

Thank you,
Pat Applicant
"""


def test_confirm_receipt_not_mapped_to_purpose() -> None:
    assert map_claim_to_purpose(CONFIRM_RECEIPT, PURPOSE_POINTS) is None


def test_one_token_overlap_does_not_map() -> None:
    assert (
        map_claim_to_purpose(
            CONFIRM_RECEIPT,
            ["Send the exhibits to the Clerk as attachments."],
        )
        is None
    )
    assert (
        map_claim_to_purpose(
            "I hope your team has a calm week ahead.", PURPOSE_POINTS
        )
        is None
    )


def test_two_token_overlap_maps_to_purpose_two() -> None:
    assert (
        map_claim_to_purpose(
            "For document exchange during the hearing, please use applicant@example.com.",
            PURPOSE_POINTS,
        )
        == "purpose[2]"
    )


def test_baseline_email_drift_flags_register_and_purpose() -> None:
    out = run_drift_check(
        BASELINE,
        channel="email",
        pin_ledger=[],
        purpose_points=PURPOSE_POINTS,
    )
    assert out["passed"] is False
    assert {"sentence": CONFIRM_RECEIPT, "reason": "not_purpose_relevant"} in out[
        "flagged_sentences"
    ]
    decl = next(
        row
        for row in out["flagged_sentences"]
        if row["reason"] == "declaration_register_in_correspondence"
    )
    assert decl["sentence"].startswith("I, Pat Applicant, am submitting")
    assert out["check_class"] == "correspondence"
    assert "sworn_body_word_count" not in out


def test_redraft_email_passes_purpose_gate() -> None:
    out = run_drift_check(
        REDRAFT,
        channel="email",
        pin_ledger=[],
        purpose_points=PURPOSE_POINTS,
    )
    assert out["passed"] is True
    assert out["flagged_sentences"] == []
    assert out["purpose_gate"] == "applied"
    assert "sworn_body_word_count" not in out
    assert out["sentence_count"] == 12
    checked = "\n".join(row["sentence"] for row in out.get("flagged_sentences", []))
    for token in ("Dear", "Subject", "Thank you", "[PHONE]"):
        assert token not in checked


def test_filing_keeps_sworn_fields() -> None:
    text = (
        "I, Pat Applicant, state:\n\n"
        "1. I received the notice (Exhibit 2).\n\n"
        "I declare under penalty of perjury under the laws of the State of California "
        "that the foregoing is true and correct."
    )
    out = run_drift_check(text, channel="filing", pin_ledger=[])
    assert out["check_class"] == "sworn"
    assert "sworn_body_word_count" in out


def test_verbatim_required_exempts_declaration_register() -> None:
    sentence = (
        "I, Pat Applicant, am submitting my signed declaration for Assessment Appeals "
        "99.0001 and 99.0002."
    )
    pin_ledger = [
        {
            "pin_id": "P1",
            "attested": True,
            "verbatim_required": True,
            "excerpt": sentence,
        }
    ]
    out = run_drift_check(sentence, channel="email", pin_ledger=pin_ledger)
    reasons = [row["reason"] for row in out["flagged_sentences"]]
    assert "declaration_register_in_correspondence" not in reasons


def _standard_email_intake(**extra: Any) -> dict[str, Any]:
    base = {
        "verbatim_notes": ["Fact one."],
        "recipient": "Clerk",
        "objective": "Submit exhibits",
        "channel": "email",
    }
    base.update(extra)
    return base


def test_schema_accepts_intake_answers_and_fact_sources() -> None:
    intake, refusal = validate_intake_payload(
        {
            "intake": _standard_email_intake(
                intake_answers=["No phone records remain."],
                fact_sources={"Exhibit 2 notice": "document"},
            )
        },
        None,
    )
    assert refusal is None
    assert intake is not None
    assert intake.intake_answers == ["No phone records remain."]
    assert intake.fact_sources == {"Exhibit 2 notice": "document"}


def test_schema_purpose_and_verbatim_required() -> None:
    intake, refusal = validate_intake_payload(
        {
            "intake": _standard_email_intake(
                purpose_points=["Hearing on Tuesday."],
                verbatim_required=[0],
            )
        },
        None,
    )
    assert refusal is None
    assert intake is not None
    assert intake.purpose_points == ["Hearing on Tuesday."]
    assert intake.verbatim_required == [0]

    _, bad = validate_intake_payload(
        {"intake": _standard_email_intake(verbatim_required=[5])},
        None,
    )
    assert bad is not None
    assert bad.get("refused") == "intake_invalid"

    intake2, refusal2 = validate_intake_payload(
        {"intake": _standard_email_intake()},
        [{"role": "user", "content": ""}],
    )
    assert refusal2 is None
    assert intake2 is not None


class _Ctx:
    def __init__(self, **options: Any) -> None:
        self.options = options


def test_assemble_intake_passes_source_message_block_to_drafters() -> None:
    inbound = "Hi Kaywan,\n\nSan Jose location fits you well."
    payload = asyncio.run(
        assemble.WritingAssembleHandler().execute(
            None,
            _Ctx(
                intake={
                    **_standard_email_intake(working_set=[]),
                    "source_message": inbound,
                },
            ),
        )
    ).json
    assert inbound in payload["source_message_block"]
    assert "Source rule:" in payload["brief_block"]


def test_assemble_email_intake_brief_and_check_class() -> None:
    payload = asyncio.run(
        assemble.WritingAssembleHandler().execute(
            None,
            _Ctx(
                intake=_standard_email_intake(
                    purpose_points=["Point A"],
                    working_set=[],
                ),
            ),
        )
    ).json
    assert payload["check_class"] == "correspondence"
    brief = payload["brief_block"]
    assert "Check class: correspondence" in brief
    assert "Register rule: verbatim_notes are facts" in brief
    assert "Review rubric (correspondence)" in brief
    assert "purpose[0]" in brief

    filing = asyncio.run(
        assemble.WritingAssembleHandler().execute(
            None,
            _Ctx(
                intake=_standard_email_intake(
                    channel="filing",
                    purpose_points=["Point A"],
                    working_set=[],
                ),
            ),
        )
    ).json
    assert "Check class: sworn" in filing["brief_block"]
    assert "Review rubric (sworn)" in filing["brief_block"]

    flagged = asyncio.run(
        assemble.WritingAssembleHandler().execute(
            None,
            _Ctx(
                intake=_standard_email_intake(
                    verbatim_required=[0],
                    working_set=[],
                ),
            ),
        )
    ).json
    assert 'verbatim_required="true"' in flagged["verbatim_notes_block"]


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _ProvCtx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class _ProvStep:
    def __init__(self) -> None:
        self.handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "draft.json",
            "judge": "semantic_judge_candidates.json",
        }


_EMPTY_JUDGE = {"pins": [], "clauses": [], "judge_unavailable": False}


def test_provenance_handler_purpose_drops_on_baseline() -> None:
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _ProvStep(),
            _ProvCtx(
                {
                    "assemble": _Out(
                        {
                            "channel": "email",
                            "purpose_points": PURPOSE_POINTS,
                            "pin_ledger": [],
                        }
                    ),
                    "draft": _Out({"draft": BASELINE, "claims": []}),
                    "semantic_judge_candidates": _Out(_EMPTY_JUDGE),
                }
            ),
        )
    ).json
    assert payload["check_class"] == "correspondence"
    assert {
        "sentence": CONFIRM_RECEIPT,
        "reason": "not_purpose_relevant",
    } in payload["purpose_drops"]
    assert "purpose_drift" in [row["type"] for row in payload["violations"]]
    assert "sworn_body_word_count" not in payload["drift_check"]


def test_not_purpose_relevant_in_finding_types() -> None:
    assert "not_purpose_relevant" in review_schema.FINDING_TYPES


_SCHEDULE_SENTENCE = "Currently my schedule stays full most of the time."
_AMANDA_PURPOSE = [
    "Reply to Amanda declining standing weekly availability and a monthly open calendar",
]


def test_verbatim_required_exempts_schedule_sentence_from_not_purpose_relevant() -> None:
    unrelated = ["Submit quarterly tax filings to the state board."]
    assert (
        drift_check_sentence(
            _SCHEDULE_SENTENCE,
            channel="email",
            exhibit_ids=set(),
            purpose_points=unrelated,
            verbatim_required_excerpts=[_SCHEDULE_SENTENCE],
        )
        != "not_purpose_relevant"
    )
    assert (
        drift_check_sentence(
            _SCHEDULE_SENTENCE,
            channel="email",
            exhibit_ids=set(),
            purpose_points=unrelated,
            verbatim_required_excerpts=(),
        )
        == "not_purpose_relevant"
    )


def test_short_verbatim_token_does_not_substring_exempt_unrelated_sentence() -> None:
    unrelated = ["Submit quarterly tax filings to the state board."]
    sentence = "I do not know whether the booking is confirmed."
    assert (
        drift_check_sentence(
            sentence,
            channel="email",
            exhibit_ids=set(),
            purpose_points=unrelated,
            verbatim_required_excerpts=["No"],
        )
        == "not_purpose_relevant"
    )
    assert (
        drift_check_sentence(
            "I do not know.",
            channel="email",
            exhibit_ids=set(),
            purpose_points=unrelated,
            verbatim_required_excerpts=["No"],
        )
        == "not_purpose_relevant"
    )
    assert (
        drift_check_sentence(
            "No",
            channel="email",
            exhibit_ids=set(),
            purpose_points=unrelated,
            verbatim_required_excerpts=["No"],
        )
        is None
    )
    assert (
        drift_check_sentence(
            "No.",
            channel="email",
            exhibit_ids=set(),
            purpose_points=unrelated,
            verbatim_required_excerpts=["No"],
        )
        is None
    )


def test_schedule_status_maps_to_availability_purpose() -> None:
    assert map_claim_to_purpose(_SCHEDULE_SENTENCE, _AMANDA_PURPOSE) is None


def test_literal_token_overlap_maps_amanda_availability_purpose() -> None:
    assert (
        map_claim_to_purpose(
            "I am declining standing weekly availability and a monthly open calendar.",
            _AMANDA_PURPOSE,
        )
        == "purpose[0]"
    )


BASELINE_INTAKE = {
    "mode": "standard",
    "channel": "email",
    "recipient": "AABHearing@cob.sccgov.org",
    "signer": "Kaywan Joseph Mansubi",
    "objective": (
        "Short cover email from Kaywan Joseph Mansubi to AABHearing@cob.sccgov.org that (1) identifies "
        "Assessment Appeals 25.7489 and 25.7490, APN 424-20-043, the property at 15571 Flintridge Dr, "
        "Los Gatos, and the reinstatement hearing on Tuesday, October 27, 2026, 9:00 AM PT, on Zoom; "
        "(2) states that the signed CCP § 2015.5 Declaration of Kaywan Joseph Mansubi Regarding Timeliness "
        "(Exhibit 1) and Exhibits 2–8 are attached; (3) lists the eight exhibits."
    ),
    "verbatim_notes": [
        "To: AABHearing@cob.sccgov.org",
        "I, Kaywan Joseph Mansubi, am submitting my signed CCP § 2015.5 Declaration of Kaywan Joseph Mansubi Regarding Timeliness (Exhibit 1) plus Exhibits 2–8 for the reinstatement hearing on Assessment Appeals 25.7489 and 25.7490.",
        "APN 424-20-043, 15571 Flintridge Dr, Los Gatos.",
        "Hearing: Tuesday, October 27, 2026, 9:00 AM PT, on Zoom.",
        "Send-by deadline: Thursday, October 22, 2026.",
        "Exhibit 1: Declaration of Kaywan Joseph Mansubi Regarding Timeliness (CCP § 2015.5)",
        "Exhibit 2: Notice of Supplemental Assessment dated 2026-01-16",
        "Exhibit 3: Notification of Base Year Value dated 2025-12-16",
        "Exhibit 4: Supplemental Tax Bills 10059585 and 10059586",
        "Exhibit 5: Invalid Closed Letters dated 2026-06-05",
        "Exhibit 6: Requests for Reinstatement stamped 2026-07-23",
        "Exhibit 7: Clerk Email of 2026-05-28 Acknowledging Documents Submitted 2026-05-27",
        "Exhibit 8: Letter to Assessor stamped 2026-04-15",
    ],
    "standing_limits": (
        "Write in first person as Kaywan Joseph Mansubi. Plain and brief. No argument on the merits. "
        "No placeholders. Use only the facts in verbatim_notes; do not add or change facts."
    ),
}


def test_provenance_fact_inventory_drift_on_paraphrase() -> None:
    load_bearing = ["I stated she called a few times a month."]
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _ProvStep(),
            _ProvCtx(
                {
                    "assemble": _Out(
                        {
                            "channel": "email",
                            "load_bearing_claims": load_bearing,
                            "pin_ledger": [],
                        }
                    ),
                    "draft": _Out({"draft": "She called monthly.", "claims": []}),
                    "semantic_judge_candidates": _Out(_EMPTY_JUDGE),
                }
            ),
        )
    ).json
    drift = payload["drift_check"]
    assert any(
        row.get("reason") == "fact_inventory_drift"
        for row in drift.get("flagged_sentences") or []
    )
    assert "purpose_drift" in [row["type"] for row in payload["violations"]]


def test_provenance_objective_only_applies_purpose_gate() -> None:
    assembled = asyncio.run(
        assemble.WritingAssembleHandler().execute(
            None,
            _Ctx(intake=BASELINE_INTAKE, working_set=[]),
        )
    ).json
    assert assembled.get("ok") is True
    assert not assembled.get("purpose_points")
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _ProvStep(),
            _ProvCtx(
                {
                    "assemble": _Out(assembled),
                    "draft": _Out({"draft": BASELINE, "claims": []}),
                    "semantic_judge_candidates": _Out(_EMPTY_JUDGE),
                }
            ),
        )
    ).json
    assert payload["drift_check"]["purpose_gate"] == "applied"
    assert {
        "sentence": CONFIRM_RECEIPT,
        "reason": "not_purpose_relevant",
    } in payload["purpose_drops"]


def test_verbatim_note_pins_baseline_p5_not_restate() -> None:
    parsed = parse_purpose_points(str(BASELINE_INTAKE["objective"]), None)
    pins = assemble._verbatim_note_pins(
        list(BASELINE_INTAKE["verbatim_notes"]),
        start=1,
        purpose_points=parsed,
    )
    by_id = {pin["pin_id"]: pin for pin in pins}
    assert by_id["P5"].get("restate") is not True
    assert by_id["P1"].get("restate") is True
    assert by_id["P2"].get("restate") is True
    assert by_id["P6"].get("restate") is True


def test_run_drift_check_exhibit_leadin_attached_are_passes() -> None:
    body = (
        "I am submitting exhibits for the hearing.\n\n"
        "Attached are:\n"
        "Exhibit 1: Declaration of Kaywan Joseph Mansubi Regarding Timeliness (CCP § 2015.5)"
    )
    out = run_drift_check(
        body,
        channel="email",
        pin_ledger=[],
        purpose=str(BASELINE_INTAKE["objective"]),
    )
    assert out["passed"] is True
    assert out["flagged_sentences"] == []


def test_baseline_intake_restate_pins_and_writer_register_rule() -> None:
    payload = asyncio.run(
        assemble.WritingAssembleHandler().execute(
            None,
            _Ctx(intake=BASELINE_INTAKE, working_set=[]),
        )
    ).json
    assert payload["verbatim_notes_block"].count('restate="true"') == 12
    assert payload["documents_block"].count('restate="true"') == 12
    assert payload["documents_block"].count('provenance="operator_account"') == 13
    assert 'verbatim_required="true"' not in payload["verbatim_notes_block"]
    assert "Copy a note word for word only when it is marked verbatim_required" in payload["packet"]
    assert "Prohibited:" in payload["brief_block"]
    assert "Email envelope:" in payload["brief_block"]
    assert "copy-verbatim" not in payload["packet"].lower()
