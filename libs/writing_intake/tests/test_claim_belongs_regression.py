"""Regression suite for claim-belongs gates (friction:39993)."""

from __future__ import annotations

import pytest
from writing_intake.claim_gates import evaluate_candidate_claim, parse_purpose_points
from writing_intake.fact_inventory import build_fact_inventory, run_drift_check
from writing_intake.schema import validate_intake_payload

pytestmark = pytest.mark.offline

AAB_PURPOSE = (
    "Support July 23, 2026 Requests for Reinstatement (Applications 25.7489 and 25.7490) "
    "regarding timeliness: conflicting Dec 16, 2025 Prop 19 and Jan 16, 2026 supplemental "
    "notices; unreturned calls to Deborah Bathurst during undisclosed emergency leave; "
    "March 25, 2026 Chase escrow discovery and April 15, 2026 Assessor visit explaining "
    "first knowledge of issue; May 27, 2026 in-person filing, June 5, 2026 closure notice, "
    "and timely July 23, 2026 reinstatement within 60 days."
)

REGRESSION_S1 = "I do not have phone records of these calls."
REGRESSION_S2_V60 = (
    "I received the January 16, 2026 notice at 15571 Flintridge Dr close to its date."
)
REGRESSION_S2_V63 = "I received the January 16, 2026 notice at 15571 Flintridge Dr shortly after its date."
REGRESSION_S3 = (
    "I do not know the date I received supplemental tax bills #10059585 and #10059586 "
    "(Exhibit 4)."
)

AAB_REGRESSION_INTAKE_ANSWERS = [
    REGRESSION_S1,
    REGRESSION_S2_V60,
]

AAB_REV6_0_SWORN_BODY = """
I, Kaywan Joseph Mansubi, state:

1. I am the applicant on Assessment Appeal Applications 25.7489 and 25.7490 and a co-owner of record of APN 424-20-043 (15571 Flintridge Dr, Los Gatos, CA 95032). I have personal knowledge of the facts below, and I submit this declaration in support of my July 23, 2026 Requests for Reinstatement (Exhibit 6).

2. A Notification of Base Year Value – Prop 19 for APN 424-20-043 dated December 16, 2025 showed a total assessed value of $1,777,400 and a total base year value of $2,800,000 (Exhibit 3). The Assessor's Notice of Supplemental Assessment dated January 16, 2026 showed a reappraised value of $2,800,000 (Exhibit 2). I received the January 16, 2026 notice at 15571 Flintridge Dr close to its date.

3. Deborah Bathurst of the Assessor's Office contacted me on her own after the title transfer. She was the only person at that office who ever did, and she never referred me to anyone else. After receiving the December 2025 notices, I called her a few times a month for help understanding what the notices said had changed. My calls were not returned. I do not have phone records of these calls.

4. On March 25, 2026, I called the Assessor's Office, the County Department of Tax and Collections, and Chase, my mortgage servicer. When I called Chase about my escrow account, I learned there were two supplemental tax bills on the property and their amounts, and Chase told me it would pay them. I did not understand what the bills were or why they had been issued, and I did not know what a supplemental assessment was or how Prop 19 applied to the property. I do not know the date I received supplemental tax bills #10059585 and #10059586 (Exhibit 4).

5. On April 15, 2026, I went to the Assessor's Office and asked to speak with someone about the notices. Rafael Aranda called me into a side office and told me Ms. Bathurst had been on emergency leave and that I should have called and asked for someone else. Until then, I did not know she was away. That same day, I left a written request for relief with the Assessor's Office (Exhibit 8).

6. By April 15, 2026, the bills had been paid from my escrow account, and my concern was how this had been handled and why so much tax had been paid. April 15, 2026 is the date I gave on my Requests for Reinstatement as my first knowledge of "this issue."

7. On May 27, 2026, I delivered my Assessment Appeal Applications in person to Dr. Amber Green at the Clerk of the Board's office. On May 28, 2026, she emailed me: “I will begin working on the documents you submitted on Wednesday May 27, 2026, when I return.” (Exhibit 7.)

8. On June 5, 2026, the Clerk notified me that applications 25.7489 and 25.7490 were closed as untimely (Exhibit 5). On July 23, 2026, within the 60 days that letter allowed, I filed Requests for Reinstatement (Exhibit 6). In those requests, I stated under penalty of perjury that I filed after the March 17, 2026 deadline because the County's notices conflicted and my calls to Ms. Bathurst went unreturned, and that my first knowledge of this issue was on April 15, 2026.

I declare under penalty of perjury under the laws of the State of California that the foregoing is true and correct.
"""

AAB_REV6_3_SWORN_BODY = AAB_REV6_0_SWORN_BODY.replace(
    "close to its date", "shortly after its date", 1
).replace(REGRESSION_S1, "")


def test_rev6_0_drops_all_three_regression_sentences() -> None:
    inv = build_fact_inventory(
        AAB_REV6_0_SWORN_BODY,
        purpose=AAB_PURPOSE,
        audience="Assessment Appeals Board",
        intake_answers=AAB_REGRESSION_INTAKE_ANSWERS,
    )
    lb = inv["load_bearing_claims"]
    assert REGRESSION_S1 not in lb
    assert REGRESSION_S2_V60 not in lb
    assert REGRESSION_S3 not in lb
    dropped_by_claim = {row["claim"]: row for row in inv["dropped_facts"]}
    assert dropped_by_claim[REGRESSION_S1]["reason"] == "volunteered_negative"
    assert dropped_by_claim[REGRESSION_S1]["provenance"] == "intake_answer"
    assert dropped_by_claim[REGRESSION_S2_V60]["reason"] == "volunteered_concession"
    assert dropped_by_claim[REGRESSION_S2_V60]["provenance"] == "intake_answer"
    assert dropped_by_claim[REGRESSION_S3]["reason"] == "volunteered_negative"
    joined = " ".join(lb)
    assert "I did not know she was away" in joined
    assert "I did not know what a supplemental assessment was" in joined


def test_rev6_3_drops_remaining_two_and_drift_check() -> None:
    inv = build_fact_inventory(
        AAB_REV6_3_SWORN_BODY,
        purpose=AAB_PURPOSE,
        audience="Assessment Appeals Board",
        intake_answers=AAB_REGRESSION_INTAKE_ANSWERS,
    )
    lb = inv["load_bearing_claims"]
    assert REGRESSION_S2_V63 not in lb
    assert REGRESSION_S3 not in lb
    drift_bad = run_drift_check(AAB_REV6_0_SWORN_BODY, channel="filing", pin_ledger=[])
    assert drift_bad["passed"] is False
    clean_lb = [c for c in lb if REGRESSION_S2_V63 not in c and REGRESSION_S3 not in c]
    clean_body = "\n\n".join(f"{i + 1}. {c}" for i, c in enumerate(clean_lb))
    drift_clean = run_drift_check(clean_body, channel="filing", pin_ledger=[])
    assert drift_clean["passed"] is True
    assert drift_clean["flagged_sentences"] == []


def test_intake_answer_precedence_over_inline_exhibit_tag() -> None:
    claim = (
        "I received the January 16, 2026 notice at 15571 Flintridge Dr close to its date "
        "(Exhibit 2)."
    )
    points = parse_purpose_points(AAB_PURPOSE)
    evaluation = evaluate_candidate_claim(
        claim,
        purpose_points=points,
        verbatim_notes=[],
        attested_sources=[],
        intake_answers=[claim],
        fact_sources=[],
        strict_attestation=False,
    )
    assert evaluation.provenance == "intake_answer"
    assert evaluation.reason == "volunteered_concession"


def test_verbatim_note_wins_over_absent_intake_answer_signature() -> None:
    claim = REGRESSION_S1
    points = parse_purpose_points(AAB_PURPOSE)
    evaluation = evaluate_candidate_claim(
        claim,
        purpose_points=points,
        verbatim_notes=[claim],
        attested_sources=[],
        intake_answers=[],
        fact_sources=[],
        strict_attestation=False,
    )
    assert evaluation.provenance == "operator_account"
    assert evaluation.reason != "intake_answer_context_only"


def test_purpose_gate_drops_unmapped_attested_fact() -> None:
    claim = "I also refinanced my primary residence in 2021."
    inv = build_fact_inventory(
        claim,
        purpose=AAB_PURPOSE,
        audience="Board",
        verbatim_notes=[claim],
        attested_sources=[],
    )
    assert inv["load_bearing_claims"] == []
    assert inv["dropped_facts"][0]["reason"] == "not_purpose_relevant"


def test_short_purpose_header_preserves_good_cause_claims() -> None:
    claim = "Until then, I did not know she was away."
    inv = build_fact_inventory(
        claim,
        purpose="AAB reinstatement declaration",
        audience="Board",
        purpose_points=[
            "Good cause for reinstatement, including when the tenant was away."
        ],
    )
    assert claim in inv["load_bearing_claims"]


def test_short_purpose_header_drops_unrelated_landlord_deposit_claims() -> None:
    inv = build_fact_inventory(
        "Ask landlord to return deposit.\nThe deposit was $2,000.",
        purpose="AAB reinstatement declaration",
        audience="Board",
    )
    assert inv["load_bearing_claims"] == []
    reasons = {row["reason"] for row in inv["dropped_facts"]}
    assert reasons & {"not_purpose_relevant", "prior_draft_not_attested"}


def test_negative_detector_preserves_past_tense_good_cause_knowledge() -> None:
    claim = (
        "I did not understand what the bills were or why they had been issued, "
        "and I did not know what a supplemental assessment was or how Prop 19 applied."
    )
    points = parse_purpose_points(AAB_PURPOSE)
    evaluation = evaluate_candidate_claim(
        claim,
        purpose_points=points,
        verbatim_notes=[],
        attested_sources=[],
        intake_answers=[],
        fact_sources=[],
        strict_attestation=False,
    )
    assert evaluation.load_bearing is True


def test_validate_intake_refuses_whitespace_purpose() -> None:
    intake, refusal = validate_intake_payload(
        {
            "intake": {
                "mode": "fact_inventory",
                "prior_draft": "I received the notice on January 16, 2026 at the property.",
                "purpose": "   \t  ",
                "objective": "  ",
                "audience": "Assessment Appeals Board",
            }
        }
    )
    assert intake is None
    assert refusal is not None
    assert refusal["refused"] == "intake_invalid"
    assert "purpose" in refusal["error"]


def test_subclause_trim_does_not_preserve_bill_receipt_negative() -> None:
    claim = (
        "I do not know the date I received Supplemental tax bills #10059585 and #10059586 "
        "(Exhibit 4); each show a bill-created date of March 1, 2026."
    )
    inv = build_fact_inventory(
        claim,
        purpose=AAB_PURPOSE,
        audience="Assessment Appeals Board",
        verbatim_notes=[],
        attested_sources=[],
        strict_attestation=True,
    )
    assert all(
        "do not know the date I received" not in item
        for item in inv["load_bearing_claims"]
    )
    reasons = {row["reason"] for row in inv["dropped_facts"]}
    assert (
        "volunteered_negative" in reasons or "unrequested_bill_created_date" in reasons
    )
    assert "I do not know the date I received Supplemental tax bills" not in " ".join(
        inv["load_bearing_claims"]
    )


def test_purpose_explicit_negative_override_keeps_required_negative() -> None:
    purpose = (
        "State that no written notice was ever received and that I do not know "
        "the date I received the supplemental tax bills."
    )
    inv = build_fact_inventory(
        REGRESSION_S3,
        purpose=purpose,
        audience="Assessment Appeals Board",
    )
    assert REGRESSION_S3 in inv["load_bearing_claims"]
    assert all(
        row.get("reason") != "volunteered_negative" for row in inv["dropped_facts"]
    )


def test_prior_draft_only_never_populates_attested_sources_or_verbatim_notes() -> None:
    inv = build_fact_inventory(
        REGRESSION_S1,
        purpose=AAB_PURPOSE,
        audience="Board",
        verbatim_notes=[],
        attested_sources=[],
        strict_attestation=True,
    )
    assert inv["verbatim_notes"] == []
    assert REGRESSION_S1 not in inv["attested_sources"]
