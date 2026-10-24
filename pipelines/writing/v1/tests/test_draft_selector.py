"""Draft selector scoring tests (offline)."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

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

draft_selector = importlib.import_module("writing_v1_handlers.draft_selector")

pytestmark = pytest.mark.offline


def test_score_draft_requires_full_pin_anchor_coverage() -> None:
    attested = [
        {
            "pin_id": "P1",
            "attested": True,
            "excerpt": "Fill was 251 when I opened Saturday",
        },
        {"pin_id": "P2", "attested": True, "excerpt": "It was 213 Sunday morning"},
    ]
    weak = "It is what it was."
    strong = "Fill was 251 when I opened Saturday. It was 213 Sunday morning."
    weak_score = draft_selector._score_draft(weak, attested)
    strong_score = draft_selector._score_draft(strong, attested)
    assert weak_score < strong_score
    assert weak_score == 0
    assert strong_score == 2


class _Out:
    def __init__(
        self,
        raw: str = "",
        *,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
        model_id: str = "local-test",
    ) -> None:
        self.raw = raw
        self.json = payload
        self.error = error
        self.model_id = model_id


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


def _select(outputs: dict[str, Any]) -> dict[str, Any]:
    payload = asyncio.run(
        draft_selector.WritingDraftSelectorHandler().execute(None, _Ctx(outputs))
    ).json
    assert payload is not None
    return payload


_PINS = [
    {
        "pin_id": "P1",
        "attested": True,
        "excerpt": "Fill was 251 when I opened Saturday",
    }
]


def test_selector_weights_first_person_over_lower_pin_coverage() -> None:
    """First-person voice outranks higher pin coverage when signer band ties."""
    pins = [
        {"pin_id": "P1", "attested": True, "excerpt": "Fill was 251 on Saturday"},
        {"pin_id": "P2", "attested": True, "excerpt": "It was 213 Sunday morning"},
    ]
    third = "Fill was 251 on Saturday. It was 213 Sunday morning."
    first = "I saw the notice on Saturday."
    selected = _select(
        {
            "assemble": _Out(payload={"pin_ledger": pins}),
            "drafter_openai": _Out(third),
            "drafter_google": _Out(first),
        }
    )
    assert selected["ok"] is True
    assert selected["draft"] == first
    assert selected["selected_role"] == "drafter_google"


def test_selector_rejects_unresolved_need_token() -> None:
    """An unresolved [NEED:] draft loses to a clean draft with fewer pins."""
    placeholder = "Fill was 251 when I opened Saturday. [NEED: date]"
    clean = "It was closed."
    selected = _select(
        {
            "assemble": _Out(payload={"pin_ledger": _PINS}),
            "drafter_openai": _Out(placeholder),
            "drafter_google": _Out(clean),
        }
    )
    assert selected["draft"] == clean
    assert selected["selected_role"] == "drafter_google"


def test_selector_refuses_when_every_draft_has_need_token() -> None:
    selected = _select(
        {
            "assemble": _Out(payload={"pin_ledger": []}),
            "drafter_openai": _Out("I filed on [NEED: date]."),
            "drafter_google": _Out("The board needs [NEED: signer]."),
        }
    )
    assert selected["ok"] is False
    assert selected["refused"] == "unresolved_need_placeholder"


def test_requested_signer_none_literal_in_brief_block_is_empty() -> None:
    """Brief line Signer: none must not become a requested signer token."""
    signer = draft_selector._requested_signer(
        {"brief_block": "Recipient: Board\nSigner: none\nObjective: Notify\n"}
    )
    assert signer == ""


def test_requested_signer_blank_line_does_not_capture_next_line() -> None:
    """Signer regex stays on one line; Voice anchor: none is not the signer."""
    signer = draft_selector._requested_signer(
        {
            "brief_block": (
                "Recipient: Board\nSigner:\nVoice anchor: none\nObjective: Notify\n"
            )
        }
    )
    assert signer == ""


def test_requested_signer_kaywan_from_brief_block() -> None:
    signer = draft_selector._requested_signer(
        {"brief_block": "Signer: Kaywan\nVoice anchor: plain\n"}
    )
    assert signer == "Kaywan"


def test_selector_none_signer_brief_first_person_beats_higher_pin_third_person() -> None:
    """Absent signer (Signer: none): word none in draft must not outrank first person."""
    pins = [
        {"pin_id": "P1", "attested": True, "excerpt": "Fill was 251 on Saturday"},
        {"pin_id": "P2", "attested": True, "excerpt": "It was 213 Sunday morning"},
    ]
    third_with_none = (
        "Fill was 251 on Saturday. It was 213 Sunday morning. "
        "There is none left to dispute."
    )
    first = "I saw the notice on Saturday."
    brief_block = (
        "Recipient: Board\nSigner: none\nVoice anchor: none\nObjective: Notify\n"
    )
    selected = _select(
        {
            "assemble": _Out(payload={"pin_ledger": pins, "brief_block": brief_block}),
            "drafter_openai": _Out(third_with_none),
            "drafter_google": _Out(first),
        }
    )
    assert selected["ok"] is True
    assert selected["draft"] == first
    assert selected["selected_role"] == "drafter_google"
    by_role = {c["role"]: c for c in selected["candidates"]}
    assert by_role["drafter_openai"]["signer_band"] == 0
    assert by_role["drafter_google"]["signer_band"] == 0


def test_selector_honors_requested_signer_kaywan() -> None:
    """A conflicting declarant loses to a Kaywan draft even with fewer pins."""
    ada = (
        "I, Ada Example, state: Fill was 251 when I opened Saturday "
        "and the ledger matched."
    )
    kaywan = "I, Kaywan, state: the notice arrived."
    selected = _select(
        {
            "assemble": _Out(payload={"pin_ledger": _PINS, "signer": "Kaywan"}),
            "drafter_openai": _Out(ada),
            "drafter_google": _Out(kaywan),
        }
    )
    assert selected["draft"] == kaywan
    assert selected["selected_role"] == "drafter_google"
    assert selected["candidates"][1]["signer_band"] == 1
    assert selected["candidates"][0]["signer_band"] == -1
