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
finalize = importlib.import_module("writing_v1_handlers.finalize")
provenance = importlib.import_module("writing_v1_handlers.provenance_check")

pytestmark = __import__("pytest").mark.offline


def test_derive_email_subject_no_hearing_without_hearing_phrase() -> None:
    assert (
        email_envelope.derive_email_subject(
            {"objective": "confirm my appointment October 27, 2026"}
        )
        == ""
    )


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


def test_email_draft_penalty_to_and_declaration() -> None:
    text = "Hello without headers.\nI, Pat Example, am submitting files."
    penalty = email_envelope.email_draft_penalty(
        text,
        channel="email",
        signer="Pat Example",
        standing_limits="",
        recipient="clerk@example.org",
    )
    assert penalty >= 6


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
