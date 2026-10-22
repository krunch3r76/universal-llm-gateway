"""Email envelope polish helpers."""

from __future__ import annotations

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
normalize_email_dashes = email_envelope.normalize_email_dashes
polish_email_draft = email_envelope.polish_email_draft

pytestmark = __import__("pytest").mark.offline


def test_normalize_email_dashes_digit_range() -> None:
    text = "Exhibits 2\u20138 attached"
    assert "2 through 8" in normalize_email_dashes(text)
    assert "\u2013" not in normalize_email_dashes(text)


def test_polish_email_adds_to_subject_and_signer() -> None:
    intake = {
        "objective": (
            "Cover for Assessment Appeals 25.7489 and 25.7490, APN 424-20-043, "
            "hearing Tuesday, October 27, 2026."
        ),
        "verbatim_notes": [],
    }
    body = "I am submitting exhibits.\n"
    out = polish_email_draft(
        body,
        recipient="clerk@example.org",
        signer="Pat Applicant",
        intake=intake,
    )
    assert out.startswith("To: clerk@example.org")
    assert "Subject:" in out
    assert "Pat Applicant" in out
