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
provenance = importlib.import_module("writing_v1_handlers.provenance_check")

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
    draft_p3 = "I am not able to provide an open calendar for next month."
    assert email_envelope.email_restate_pin_satisfied(draft_p3, pin_p3)


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
