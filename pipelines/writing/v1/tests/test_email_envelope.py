"""Email envelope polish helpers."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

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

email_envelope = importlib.import_module("writing_v1_handlers.email_envelope")
assemble = importlib.import_module("writing_v1_handlers.assemble")
draft_selector = importlib.import_module("writing_v1_handlers.draft_selector")
finalize = importlib.import_module("writing_v1_handlers.finalize")
provenance = importlib.import_module("writing_v1_handlers.provenance_check")

_PAT_VERBATIM_DECLARATION = (
    "I, Pat Applicant, am submitting my signed declaration for "
    "Assessment Appeals 99.0001 and 99.0002 during 7–3."
)

_gates_path = Path(__file__).resolve().parent / "test_channel_purpose_gates.py"
_gates_spec = importlib.util.spec_from_file_location("_channel_gates", _gates_path)
assert _gates_spec is not None and _gates_spec.loader is not None
_gates_mod = importlib.util.module_from_spec(_gates_spec)
_gates_spec.loader.exec_module(_gates_mod)
BASELINE_INTAKE = _gates_mod.BASELINE_INTAKE

pytestmark = __import__("pytest").mark.offline


def test_derive_email_subject_baseline_intake_returns_empty() -> None:
    assert email_envelope.derive_email_subject(BASELINE_INTAKE) == ""


def test_derive_email_subject_in_case_it_helps_returns_empty() -> None:
    assert (
        email_envelope.derive_email_subject(
            {
                "objective": (
                    "In case it helps, my account was charged twice on October 27, 2026"
                )
            }
        )
        == ""
    )


def test_derive_email_subject_two_dates_status_hearing_returns_empty() -> None:
    assert (
        email_envelope.derive_email_subject(
            {
                "objective": (
                    "Status Hearing. The notice dated September 1, 2026 set it for "
                    "October 27, 2026"
                )
            }
        )
        == ""
    )


def test_derive_email_subject_from_verbatim_note_subject_line() -> None:
    assert (
        email_envelope.derive_email_subject(
            {
                "objective": "cover email",
                "verbatim_notes": [
                    "Body line one.",
                    "Subject: Exhibits for Appeal 99.0001",
                ],
            }
        )
        == "Exhibits for Appeal 99.0001"
    )


def test_derive_email_subject_from_explicit_intake_subject_field() -> None:
    assert (
        email_envelope.derive_email_subject(
            {
                "subject": (
                    "Assessment Appeals 25.7489 and 25.7490, APN 424-20-043"
                )
            }
        )
        == "Assessment Appeals 25.7489 and 25.7490, APN 424-20-043"
    )


def test_derive_email_subject_no_hearing_without_hearing_phrase() -> None:
    assert (
        email_envelope.derive_email_subject(
            {"objective": "confirm my appointment October 27, 2026"}
        )
        == ""
    )


def test_email_restate_pin_satisfied_amanda_vms_temporal_monthly() -> None:
    pin_p2 = (
        "I do not have standing availability on any day of the week. "
        "My schedule stays full most of the time."
    )
    draft_p2 = (
        "I do not have standing availability on any day of the week. "
        "Right now my schedule stays full most of the time."
    )
    assert email_envelope.email_restate_pin_satisfied(draft_p2, pin_p2)
    pin_p3 = "I am not able to provide a monthly open calendar."
    draft_p3 = "I am not able to provide a monthly open calendar each month."
    assert email_envelope.email_restate_pin_satisfied(draft_p3, pin_p3)


def test_email_restate_pin_currently_requires_token_or_right_now_synonym() -> None:
    pin_currently = "Currently my schedule stays full most of the time."
    assert email_envelope.email_restate_pin_satisfied(
        "My schedule stays full most of the time.", pin_currently
    )
    assert email_envelope.email_restate_pin_satisfied(
        "Right now my schedule stays full most of the time.", pin_currently
    )
    pin_provide = "I am not able to provide a monthly open calendar."
    assert email_envelope.email_restate_pin_satisfied(
        "I can't send a monthly open calendar.", pin_provide
    )

    amanda_draft = (
        "I don't have standing availability on any day of the week. "
        "My schedule stays full most of the time, so I can't send a monthly open calendar."
    )
    amanda_pins = (
        "I do not have standing availability on any day of the week.",
        pin_currently,
        pin_provide,
    )
    for pin in amanda_pins:
        assert email_envelope.email_restate_pin_satisfied(amanda_draft, pin)

    assert not email_envelope.email_restate_pin_satisfied(
        "My schedule stays full most of the time.",
        pin_currently,
        verbatim_required=True,
    )
    assert not email_envelope.email_restate_pin_satisfied(
        "I can't send a monthly open calendar.",
        pin_provide,
        verbatim_required=True,
    )
    assert not email_envelope.email_restate_pin_satisfied(
        "I don't have standing availability on any day of the week.",
        "I do not have standing availability on any day of the week.",
        verbatim_required=True,
    )
    assert email_envelope.email_restate_pin_satisfied(
        pin_currently,
        pin_currently,
        verbatim_required=True,
    )
    assert email_envelope.email_restate_pin_satisfied(
        pin_provide,
        pin_provide,
        verbatim_required=True,
    )

    for flipped in (
        "I can send a monthly open calendar.",
        "I am able to provide a monthly open calendar.",
        (
            "I don't have standing availability on any day of the week. "
            "My schedule stays full most of the time, so I can send a monthly open calendar."
        ),
        "My schedule stays full, I don't have open days, so I can send a monthly open calendar.",
        "Not a problem, I can send a monthly open calendar.",
        (
            "I don't have standing availability on any day of the week, "
            "but I can send a monthly open calendar."
        ),
        "I can't send a monthly open calendar. I can send a monthly open calendar.",
        "I can send a monthly open calendar. I can't send a monthly open calendar.",
        (
            "I don't have standing availability on any day of the week "
            "but I can send a monthly open calendar."
        ),
        (
            "I don't have standing availability on any day of the week; "
            "I can send a monthly open calendar."
        ),
        (
            "I don't have standing availability on any day of the week \u2014 "
            "I can send a monthly open calendar."
        ),
        (
            "I don't have standing availability on any day of the week\n"
            "I can send a monthly open calendar."
        ),
    ):
        assert not email_envelope.email_restate_pin_satisfied(flipped, pin_provide)

    pin_standing = "I do not have standing availability on any day of the week."
    assert not email_envelope.email_restate_pin_satisfied(
        "I don't mind, I have standing availability on any day of the week.",
        pin_standing,
    )
    assert not email_envelope.email_restate_pin_satisfied(
        "I don't mind but I have standing availability on any day of the week.",
        pin_standing,
    )

    assert email_envelope.email_restate_pin_satisfied(
        "See <brief> section.",
        "See &lt;brief> section. [truncated]",
        verbatim_required=True,
    )


def test_score_draft_verbatim_required_email_restate_pin() -> None:
    pin_provide = "I am not able to provide a monthly open calendar."
    verbatim_pin = [
        {
            "pin_id": "P1",
            "attested": True,
            "verbatim_required": True,
            "excerpt": pin_provide,
        }
    ]
    paraphrase = "I can't send a monthly open calendar."
    assert draft_selector._score_draft(paraphrase, verbatim_pin, channel="email") == 0
    assert (
        draft_selector._score_draft(pin_provide, verbatim_pin, channel="email") == 1
    )


def test_email_restate_pin_satisfied_contraction_and_decline_synonyms() -> None:
    pin_p2 = (
        "I do not have standing availability on any day of the week because "
        "my schedule stays full most of the time."
    )
    draft_p2 = (
        "I don't have standing availability on any day of the week because "
        "my schedule stays full most of the time."
    )
    assert email_envelope.email_restate_pin_satisfied(draft_p2, pin_p2)
    pin_p3 = "I am not able to provide a monthly open calendar."
    draft_p3 = (
        "I also can't provide a monthly open calendar for next month."
    )
    assert email_envelope.email_restate_pin_satisfied(draft_p3, pin_p3)


def test_email_restate_pin_satisfied_p2_plus_and_through() -> None:
    pin = (
        "I, Kaywan Joseph Mansubi, am submitting my CCP § 2015.5 Declaration of "
        "Kaywan Joseph Mansubi Regarding Timeliness (Exhibit 1) plus Exhibits 2–8 "
        "for the reinstatement hearing on Assessment Appeals 25.7489 and 25.7490."
    )
    draft = (
        "I am submitting my CCP § 2015.5 Declaration of Kaywan Joseph Mansubi "
        "Regarding Timeliness (Exhibit 1) and Exhibits 2 through 8 for the "
        "reinstatement hearing on Assessment Appeals 25.7489 and 25.7490."
    )
    assert email_envelope.email_restate_pin_satisfied(draft, pin)


def test_polish_rewrites_formal_openings() -> None:
    intake: dict = {"objective": "notify", "verbatim_notes": []}
    formal = (
        "I, Kaywan Joseph Mansubi, am submitting the attached exhibits for the hearing."
    )
    impersonal = "This correspondence serves to submit the attached exhibits for the hearing."
    for body in (formal, impersonal):
        out = email_envelope.polish_email_draft(
            body,
            recipient="",
            signer="",
            intake=intake,
        )
        assert "I am submitting the attached exhibits" in out
        assert "I, Kaywan Joseph Mansubi, am submitting" not in out
        assert "This correspondence serves" not in out


def test_ensure_envelope_replaces_short_signer_in_place() -> None:
    body = "Body text.\n\nThank you,\nKaywan Mansubi"
    out = email_envelope.ensure_email_envelope(
        body,
        recipient="",
        signer="Kaywan Joseph Mansubi",
        intake={"objective": "x"},
    )
    assert out.count("Kaywan Joseph Mansubi") == 1
    assert "Kaywan Mansubi" not in out


def test_email_draft_penalty_missing_to_isolated() -> None:
    body = (
        "Subject: Test\n\n"
        "I am submitting the records.\n\n"
        "Thank you,\nKaywan Joseph Mansubi"
    )
    recipient = "AABHearing@cob.sccgov.org"
    signer = "Kaywan Joseph Mansubi"
    without_to = email_envelope.email_draft_penalty(
        body,
        channel="email",
        signer=signer,
        standing_limits="",
        recipient=recipient,
    )
    with_to = email_envelope.email_draft_penalty(
        f"To: {recipient}\n\n{body}",
        channel="email",
        signer=signer,
        standing_limits="",
        recipient=recipient,
    )
    assert without_to == 3
    assert with_to == 0


def test_provenance_email_subject_missing_unresolved() -> None:
    class _Out:
        def __init__(self, payload: dict) -> None:
            self.json = payload

    class _Ctx:
        def __init__(self, outputs: dict) -> None:
            self.outputs = outputs

    class _Step:
        handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "draft.json",
        }

    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _Step(),
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "channel": "email",
                            "pin_ledger": [],
                            "intake": {"objective": "confirm my appointment"},
                        }
                    ),
                    "draft": _Out(
                        {
                            "draft": "Hello,\n\nNo subject here.",
                            "claims": [],
                        }
                    ),
                }
            ),
        )
    ).json
    types = [row["type"] for row in payload["violations"]]
    assert "email_subject_missing" in types


def test_provenance_email_verbatim_pin_survives_email_polish() -> None:
    class _Out:
        def __init__(self, payload: dict) -> None:
            self.json = payload

    class _Ctx:
        def __init__(self, outputs: dict) -> None:
            self.outputs = outputs

    class _Step:
        handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "draft.json",
        }

    verbatim_excerpt = (
        "I, Pat Applicant, am submitting my signed declaration for "
        "Assessment Appeals 99.0001 and 99.0002 during 7–3."
    )
    good_draft = (
        "Subject: Assessment Appeals\n\n"
        f"{verbatim_excerpt}"
    )
    pin_ledger = [
        {
            "pin_id": "V1",
            "attested": True,
            "verbatim_required": True,
            "excerpt": verbatim_excerpt,
        }
    ]
    ctx = _Ctx(
        {
            "assemble": _Out(
                {
                    "channel": "email",
                    "pin_ledger": pin_ledger,
                    "intake": {"objective": "submit declaration"},
                }
            ),
            "draft": _Out({"draft": good_draft, "claims": []}),
        }
    )
    good_payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(_Step(), ctx)
    ).json
    good_types = [row["type"] for row in good_payload["violations"]]
    assert "verbatim_pin_modified" not in good_types

    bad_draft = good_draft.replace("signed", "updated")
    bad_ctx = _Ctx(
        {
            "assemble": _Out(
                {
                    "channel": "email",
                    "pin_ledger": pin_ledger,
                    "intake": {"objective": "submit declaration"},
                }
            ),
            "draft": _Out({"draft": bad_draft, "claims": []}),
        }
    )
    bad_payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(_Step(), bad_ctx)
    ).json
    bad_types = [row["type"] for row in bad_payload["violations"]]
    assert "verbatim_pin_modified" in bad_types


def test_email_restate_pin_verbatim_truncated_mid_word_n1() -> None:
    suffix = "calendar month end."
    note = ("ok " * 500)[:1185] + suffix
    assert len(note) > 1200
    keep = 1200 - len(" [truncated]")
    assert note[1185:1193] == "calendar"
    pin_text = assemble._truncate(note)
    assert pin_text.endswith("cal [truncated]")
    assert pin_text == note[:keep] + " [truncated]"
    assert len(pin_text) == 1200
    draft = f"Subject: Appeal\n\n{note}\n"
    assert email_envelope.email_restate_pin_satisfied(
        draft, pin_text, verbatim_required=True
    )


def test_polish_preserves_verbatim_required_span_n2() -> None:
    body = (
        "Subject: Assessment Appeals\n\n"
        f"{_PAT_VERBATIM_DECLARATION}\n\nThank you,\nPat Applicant"
    )
    pin_ledger = [
        {
            "pin_id": "V1",
            "attested": True,
            "verbatim_required": True,
            "excerpt": _PAT_VERBATIM_DECLARATION,
        }
    ]
    intake: dict = {"objective": "submit declaration", "verbatim_notes": []}
    polished = email_envelope.polish_email_draft(
        body,
        recipient="clerk@example.gov",
        signer="Pat Applicant",
        intake=intake,
        pin_ledger=pin_ledger,
    )
    assert _PAT_VERBATIM_DECLARATION in polished

    assemble_payload = {
        "channel": "email",
        "pin_ledger": pin_ledger,
        "intake": {
            "objective": "submit declaration",
            "recipient": "clerk@example.gov",
            "signer": "Pat Applicant",
        },
    }
    finalized = finalize._polish_email_draft_if_needed(assemble_payload, body)
    assert _PAT_VERBATIM_DECLARATION in finalized

    intake_indexed: dict = {
        "objective": "submit declaration",
        "verbatim_notes": [_PAT_VERBATIM_DECLARATION],
        "verbatim_required": [0],
    }
    polished_indexed = email_envelope.polish_email_draft(
        body,
        recipient="clerk@example.gov",
        signer="Pat Applicant",
        intake=intake_indexed,
    )
    assert _PAT_VERBATIM_DECLARATION in polished_indexed


def test_email_restate_pin_wont_be_able_to_maps_n4() -> None:
    pin_provide = "I am not able to provide a monthly open calendar."
    draft = "I won't be able to send a monthly open calendar."
    assert email_envelope.email_restate_pin_satisfied(
        draft, pin_provide, verbatim_required=False
    )
    past_draft = "I was not able to send a monthly open calendar last month."
    assert not email_envelope.email_restate_pin_satisfied(
        past_draft, pin_provide, verbatim_required=False
    )


def test_email_restate_pin_provide_refusal_valid_and_false_positives_n3() -> None:
    pin_provide = "I am not able to provide a monthly open calendar."
    valid_drafts = (
        "As you can see, I can't send a monthly open calendar.",
        "I don't think I can send a monthly open calendar.",
        "I can't see a way to send a monthly open calendar.",
        "I can't wait to hear back, but I can't send a monthly open calendar.",
        "I can't currently send a monthly open calendar.",
        "I can't really send a monthly open calendar.",
        "Right now I can't reliably send a monthly open calendar.",
        "I'm not able to commit to or send a monthly open calendar.",
        (
            "My schedule stays full most of the time, so I can't currently send "
            "a monthly open calendar."
        ),
        "I did not think I can send a monthly open calendar.",
    )
    for draft in valid_drafts:
        assert email_envelope.email_restate_pin_satisfied(
            draft, pin_provide, verbatim_required=False
        )
    false_drafts = (
        "I cannot wait to provide a monthly open calendar.",
        "I can not only provide a monthly open calendar, I can send it today.",
        "I do not think you saw my note, so I will provide a monthly open calendar.",
        "I can not see why I would not provide a monthly open calendar.",
        "I can't help but send a monthly open calendar.",
        "I don't think I'm not able to send a monthly open calendar.",
        "I don't think I'm unable to send a monthly open calendar.",
        "I can't see why I can't send a monthly open calendar.",
        "I can not see how I could refuse to send a monthly open calendar.",
        "I can't forget to send a monthly open calendar.",
        "I can't fail to send a monthly open calendar.",
        "I can't deny I send a monthly open calendar.",
        "I can not believe I send a monthly open calendar.",
    )
    for draft in false_drafts:
        assert not email_envelope.email_restate_pin_satisfied(
            draft, pin_provide, verbatim_required=False
        )


def test_email_restate_pin_affirmative_schedule_polarity_n5() -> None:
    pin_currently = "Currently my schedule stays full most of the time."
    for negated in (
        "My schedule never stays full most of the time.",
        "My schedule no longer stays full most of the time.",
    ):
        assert not email_envelope.email_restate_pin_satisfied(
            negated, pin_currently, verbatim_required=False
        )

    pin_provide = "I am not able to provide a monthly open calendar."
    amanda_draft = (
        "I don't have standing availability on any day of the week. "
        "My schedule stays full most of the time, so I can't send a monthly open calendar."
    )
    amanda_pins = (
        "I do not have standing availability on any day of the week.",
        pin_currently,
        pin_provide,
    )
    for pin in amanda_pins:
        assert email_envelope.email_restate_pin_satisfied(amanda_draft, pin)
