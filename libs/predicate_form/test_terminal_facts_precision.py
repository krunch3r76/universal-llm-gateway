"""Regression fixtures for terminal_facts precision (arc 6386 slice 5b-fix)."""

from __future__ import annotations

from predicate_form.action_enrichment import enrich_action_predicate_from_claim

_ENTITY = "account:chase-mortgage-8787"
_CASE_ENTITY = "case:chase-escrow-flintridge-2026"

_A8909_CLAIM = (
    "On 2026-04-13, Nell Cruz opened Chase case ECW260413-02188 and documented "
    "work order #953902037 for a request to extend the escrow shortage spread "
    "beyond 12 months, but there was no guarantee of approval; the "
    "contemporaneous record should not be read as Chase having already approved "
    "or begun implementing a 24-36 month spread."
)

_A27045_CLAIM = (
    "AUG 1 OPERATIONAL FORK (operator, 2026-07-30) — a live EO ask exists and "
    "is unanswered two days before the payment date; the open question is what "
    "to pay Saturday, not what to argue. Established by the bridge "
    "reconstruction at cortex://notes/system/investigations/"
    "chase-correspondence-bridge-recon-2026-07-30.md (cursor-auto, healthy-bridge "
    "read, agent-bus:6353 t4): the operative ask went out 2026-07-24 15:41 PT "
    "from biz@k-1.me to the WRONG address chase.mb.executive.office@chase.com "
    "(msg <a255c716367c6e9af8cf78868389f9c2@k-1.me>), and was resent 2026-07-27 "
    "15:20 PT from kaywan@mansubi.com to the correct executive.office@chase.com "
    "(msg <CAC1uNM0RFEzGQTNp=r8i32PBAb3K5ZhOd6sLWxFDPUkSqfg2zg@mail.gmail.com>, "
    ".eml of record cortex://documents/finance/chase/"
    "20260727-eo-new-case-request-SENT-4024208787.eml). It asks EO to open a "
    "new case and REINSTATE the April 27 2026 temporary payment change at "
    "$5,530.22 for the Aug 1 and Sep 1 due dates with full payment resuming Oct 1, "
    "and it discloses the 2026-06-05 untimely closure, the 2026-07-23 "
    "clerk-stamped RFRs, and the 2026-07-13 closure of ECW260702, attaching both "
    "RFRs and the 4/16 escrow analysis. STATE AS OF NOW: no inbound Chase reply "
    "on any dispute channel (0 messages from mortgage.escalations@chase.com and 0 "
    "from executive.office@chase.com on the healthy index), no new EO case number, "
    "no written ack; phone track sits at Laura Lambert (EO) after the 7/28 "
    "voicemail exchange (a:26943). THE FORK: Aug 1 2026 is a Saturday and the "
    "scheduled amount is ~$8,985.42 while the requested amount is $5,530.22. Per "
    "the CFPB Small Entity Compliance Guide (mortgage servicing), a PERIODIC "
    "PAYMENT is an amount sufficient to cover principal, interest and escrow for "
    "a given billing cycle, and the servicer must credit a periodic payment as of "
    "the day of receipt. INFERENCE, not a retrieved rule: a remittance of "
    "$5,530.22 against a scheduled $8,985.42 is not a periodic payment, so the "
    "day-of-receipt crediting protection does not attach to it and it is exposed "
    "to suspense-account treatment and delinquency reporting if EO has not "
    "approved the reduction in writing by the due date. That risk is the "
    "actionable question for Saturday and it is unresolved. NOT YET VERIFIED: the "
    "specific Reg X / Reg Z treatment of partial payments and suspense accounts — "
    "a targeted retrieval timed out and must be re-run before any of this is "
    "relied on. OPEN QUESTION FOR OPERATOR, not a criticism: "
    "todo:chase-august-payment-cliff-parallel carries the constraint 'do NOT "
    "frame as continue-temp-payment-adjustment' because that framing was denied "
    "(a:23269, a:20701), yet the operative 7/24-7/27 letter is framed as "
    "reinstatement of a previously APPROVED temporary payment change. Those may "
    "be materially different asks — leaning on a prior approval as an existing "
    "fact is stronger than requesting a continuation — but the tension between "
    "the recorded constraint and the shipped letter is not dispositioned "
    "anywhere. RELATIONSHIP TO a:27040: the Reg X 1024.17(f)(3)(ii) option-"
    "election argument is now a FALLBACK for if EO declines or stays silent, not "
    "the primary ask. The primary need is an answer."
)

_A26054_CLAIM = (
    "Verbatim June 26 2026 denial language, read directly from the operator's "
    "contemporaneous call note (Chase Escalations, rep 'Matthew', ~12:30pm, case "
    "ECW260413-02188). The note explicitly flags its own fidelity: 'The "
    "following transcription was not entirely verbatim and was typed as the "
    "speaker (Matthew) spoke fast'. Within that transcription block the two "
    "relevant lines appear consecutively and stand alone: 'We are unable to "
    "spread the escrow shortage over more than 12 montrhs' [sic] followed "
    "immediately by 'the request for the lower payment was denied'. The next "
    "lines are 'april 16, 2026' and '$41k shortage will increase payment to $8k'. "
    "IMPORTANT PROVENANCE LIMIT: the note records these as two adjacent "
    "statements, NOT as a stated premise-and-conclusion. Matthew is not recorded "
    "saying the 12-month limit WAS the ground for the lower-payment denial; the "
    "causal link is a reader's inference from adjacency. A letter sentence telling "
    "Chase that the 12-month limit 'is the ground I was given for the June 26 "
    "denial' therefore attributes to Chase a reasoning step the note does not "
    "record Chase making, and it would be quoting that reasoning back at Chase. "
    "The safer construction is to state the two facts separately: Chase declined "
    "to spread the shortage beyond twelve months, and Chase denied the "
    "lower-payment request on June 26."
)

_A20701_CLAIM = (
    "WO 956908029 / lower-payment request — DENIED, confirmed 2026-06-26. "
    "On the 2026-06-26 ~12:30 PM Chase Escalations call (case ECW260413-02188, "
    "rep 'Matthew', who reviewed Janet's notes), Chase stated it is unable to "
    "spread the escrow shortage beyond 12 months and that the request for the "
    "lower payment was DENIED."
)

_A7738_CLAIM = (
    "WO #953902037 — Kaywan's request to extend escrow shortage spread beyond "
    "the standard 12-month RESPA floor — was DENIED on the 2026-04-29 Nell Cruz "
    "callback. Nell stated: 'we are unable to spread the escrow shortage over "
    "12 months.'"
)


def test_a8909_does_not_emit_granted() -> None:
    preview = enrich_action_predicate_from_claim(
        _A8909_CLAIM,
        _ENTITY,
        assertion_id=8909,
        observed_at="2026-05-08T13:28:56Z",
    )
    if preview is None:
        return
    assert preview.functor != "granted"
    assert "granted" not in preview.predicate_form


def test_a27045_does_not_emit_denied_escrow_analysis() -> None:
    preview = enrich_action_predicate_from_claim(
        _A27045_CLAIM,
        _CASE_ENTITY,
        assertion_id=27045,
        observed_at="2026-07-30T02:19:54.619235+00:00",
        valid_from="2026-07-30",
    )
    if preview is None:
        return
    assert not (
        preview.functor == "denied" and preview.action == "escrow_analysis"
    )


def test_a26054_date_is_june_26_not_observed_at() -> None:
    preview = enrich_action_predicate_from_claim(
        _A26054_CLAIM,
        _ENTITY,
        assertion_id=26054,
        observed_at="2026-07-24T20:51:16.973021+00:00",
        valid_from="2026-06-26T00:00:00Z",
    )
    assert preview is not None
    assert preview.functor == "denied"
    assert "2026-07-24" not in preview.predicate_form
    assert preview.predicate_form.endswith("2026-06-26)") or ", unknown)" in preview.predicate_form


def test_a20701_still_emits_denied_spread_extension() -> None:
    preview = enrich_action_predicate_from_claim(
        _A20701_CLAIM,
        _ENTITY,
        assertion_id=20701,
        observed_at="2026-06-26T19:54:57Z",
        valid_from="2026-06-26",
        epistemic_state="committed",
    )
    assert preview is not None
    assert preview.predicate_form == "denied(spread_extension, chase, 2026-06-26)"
    assert preview.claim_excerpt is not None
    assert len(preview.claim_excerpt) <= 200


def test_a7738_still_emits_denied_spread_extension() -> None:
    preview = enrich_action_predicate_from_claim(
        _A7738_CLAIM,
        _ENTITY,
        assertion_id=7738,
        observed_at="2026-04-29T17:10:00Z",
        valid_from="2026-04-29",
        epistemic_state="staged",
    )
    assert preview is not None
    assert preview.predicate_form == "denied(spread_extension, chase, 2026-04-29)"


def test_enrichment_carries_derivation_source() -> None:
    preview = enrich_action_predicate_from_claim(
        _A20701_CLAIM,
        _ENTITY,
        assertion_id=20701,
        valid_from="2026-06-26",
    )
    assert preview is not None
    assert preview.source == "action_enrichment_template_v0"
