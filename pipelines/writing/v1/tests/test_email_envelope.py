"""Email envelope polish helpers (verbatim restate + mechanical penalties)."""

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
finalize = importlib.import_module("writing_v1_handlers.finalize")
provenance = importlib.import_module("writing_v1_handlers.provenance_check")

_gates_path = Path(__file__).resolve().parent / "test_channel_purpose_gates.py"
_gates_spec = importlib.util.spec_from_file_location("_channel_gates", _gates_path)
assert _gates_spec is not None and _gates_spec.loader is not None
_gates_mod = importlib.util.module_from_spec(_gates_spec)
_gates_spec.loader.exec_module(_gates_mod)
BASELINE_INTAKE = _gates_mod.BASELINE_INTAKE

_PAT_VERBATIM_DECLARATION = (
    "I, Pat Applicant, am submitting my signed declaration for "
    "Assessment Appeals 99.0001 and 99.0002 during 7–3."
)

pytestmark = __import__("pytest").mark.offline


def test_derive_email_subject_baseline_intake_returns_empty() -> None:
    assert email_envelope.derive_email_subject(BASELINE_INTAKE) == ""


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


def test_email_restate_pin_verbatim_truncated_mid_word_n1() -> None:
    suffix = "calendar month end."
    note = ("ok " * 500)[:1185] + suffix
    pin_text = assemble._truncate(note)
    draft = f"Subject: Appeal\n\n{note}\n"
    assert email_envelope.email_restate_pin_satisfied(draft, pin_text)


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


def test_correspondence_register_penalty_delegated_to_judge() -> None:
    assert (
        email_envelope.correspondence_register_penalty(
            "Unfortunately I decline.",
            channel="email",
        )
        == 0
    )


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
            "judge": "semantic_judge_candidates.json",
        }

    verbatim_excerpt = _PAT_VERBATIM_DECLARATION
    good_draft = f"Subject: Assessment Appeals\n\n{verbatim_excerpt}"
    pin_ledger = [
        {
            "pin_id": "V1",
            "attested": True,
            "verbatim_required": True,
            "excerpt": verbatim_excerpt,
        }
    ]
    judge = {"pins": [], "clauses": [], "judge_unavailable": False}
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
            "semantic_judge_candidates": _Out(judge),
        }
    )
    good_payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(_Step(), ctx)
    ).json
    assert "verbatim_pin_modified" not in [
        row["type"] for row in good_payload["violations"]
    ]


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
