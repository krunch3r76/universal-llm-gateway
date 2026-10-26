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
email_envelope = importlib.import_module("writing_v1_handlers.email_envelope")

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


def test_selector_rejects_link_claim_when_source_message_has_no_url() -> None:
    source = "Hi Kaywan, your San Jose location is a good fit for this role."
    link_draft = 'The application link did not come through. Please send the apply link.'
    clean = "I am interested. San Jose works well for me. What are the next steps?"
    selected = _select(
        {
            "assemble": _Out(
                payload={
                    "pin_ledger": _PINS,
                    "intake": {"source_message": source},
                }
            ),
            "drafter_openai": _Out(link_draft),
            "drafter_google": _Out(clean),
        }
    )
    assert selected["ok"] is True
    assert selected["draft"] == clean
    assert selected["selected_role"] == "drafter_google"


_MARIA_RX_PINS = [
    {
        "pin_id": "P1",
        "attested": True,
        "excerpt": "declining the PHC Petaluma shift dates listed",
    },
    {
        "pin_id": "P2",
        "attested": True,
        "excerpt": "not available for those Petaluma dates",
    },
]


def test_maria_rxrelief_natural_sms_decline_beats_cold_and_over_answer() -> None:
    """Gold SMS decline outranks Hermes cold HR and Anthropic reassurance bolt-ons."""
    gold = (
        "Maria, I can't take those Petaluma shifts. Thanks for sending them. Kaywan"
    )
    hermes = (
        "Maria, I am declining the PHC Petaluma shift dates you listed as they are "
        "not available for me. Please let me know if there are other shifts we can "
        "schedule. Thank you, Kaywan"
    )
    anthropic = (
        "Maria, I can't cover those Petaluma dates. I'm still happy to stay on "
        "with RxRelief. Thanks, Kaywan"
    )
    assemble = {
        "pin_ledger": _MARIA_RX_PINS,
        "channel": "sms",
        "signer": "Kaywan",
        "objective": "Decline the listed Petaluma shift dates only.",
        "intake": {
            "channel": "sms",
            "objective": "Decline the listed Petaluma shift dates only.",
            "verbatim_notes": [],
        },
    }
    selected = _select(
        {
            "assemble": _Out(payload=assemble),
            "drafter_openai": _Out(hermes),
            "drafter_local_hermes": _Out(anthropic),
            "drafter_anthropic": _Out(gold),
        }
    )
    assert selected["ok"] is True
    assert selected["draft"] == gold
    assert selected["selected_role"] == "drafter_anthropic"
    by_role = {c["role"]: c for c in selected["candidates"]}
    assert by_role["drafter_anthropic"]["score"] >= by_role["drafter_openai"]["score"]
    assert (
        by_role["drafter_anthropic"]["email_penalty"]
        < by_role["drafter_openai"]["email_penalty"]
    )


@pytest.mark.parametrize(
    ("draft", "expected_min_penalty"),
    [
        ("I'm declining the Petaluma shifts.", 3),
        ("I'm not available for the Petaluma dates.", 3),
        ("Unfortunately I can't make it.", 3),
        ("I'm still happy to stay on with RxRelief.", 3),
        ("Maria — can't take those shifts.", 3),
    ],
)
def test_correspondence_register_penalty_cold_and_over_answer(
    draft: str, expected_min_penalty: int
) -> None:
    penalty = email_envelope.correspondence_register_penalty(
        draft,
        channel="sms",
        objective="Decline the listed Petaluma shift dates only.",
    )
    assert penalty >= expected_min_penalty


def test_correspondence_register_penalty_negated_objective_still_penalizes_over_answer() -> None:
    objective = (
        "Decline the Petaluma dates; don't offer other shifts or future availability"
    )
    notes = "the future Petaluma dates"
    penalty = email_envelope.correspondence_register_penalty(
        "Please let me know if there are other shifts we can schedule.",
        channel="sms",
        objective=objective,
        verbatim_notes=notes,
    )
    assert penalty >= 3


def test_correspondence_register_penalty_affirmative_objective_allows_over_answer() -> None:
    objective = (
        "Decline the Petaluma dates and offer future shifts we can schedule"
    )
    penalty = email_envelope.correspondence_register_penalty(
        "Please let me know if there are other shifts we can schedule.",
        channel="sms",
        objective=objective,
    )
    assert penalty == 0


def test_signer_band_sms_neutral_when_signer_in_body() -> None:
    signer = "Kaywan"
    draft = (
        "Hi Maria, I won't be able to cover the Petaluma dates you sent. Thank you, Kaywan"
    )
    assert (
        draft_selector._signer_band(draft, signer, channel="sms") == 0
    )
    assert draft_selector._signer_band(draft, signer, channel="email") == 1


def test_sms_trailing_signer_name_loses_to_neutral_thanks() -> None:
    """SMS: no signature name beats Thank you, Kaywan when pins tie."""
    pins = _MARIA_RX_PINS
    neutral = (
        "Hi Maria, I won't be able to cover the Petaluma dates you sent. Thank you."
    )
    signed = (
        "Hi Maria, I won't be able to cover the Petaluma dates you sent. "
        "Thank you, Kaywan"
    )
    assemble = {
        "pin_ledger": pins,
        "channel": "sms",
        "signer": "Kaywan",
        "objective": "Decline the listed Petaluma shift dates only.",
        "intake": {
            "channel": "sms",
            "signer": "Kaywan",
            "objective": "Decline the listed Petaluma shift dates only.",
            "verbatim_notes": [],
        },
    }
    selected = _select(
        {
            "assemble": _Out(payload=assemble),
            "drafter_openai": _Out(signed),
            "drafter_google": _Out(neutral),
        }
    )
    assert selected["draft"] == neutral
    assert selected["selected_role"] == "drafter_google"


def test_email_still_rewards_signer_closing() -> None:
    pins = _PINS
    unsigned = "To: Board\nSubject: Notice\n\nI saw the notice.\n\nThank you,\n"
    signed = (
        "To: Board\nSubject: Notice\n\nI saw the notice.\n\nThank you,\nKaywan"
    )
    assemble = {
        "pin_ledger": pins,
        "channel": "email",
        "signer": "Kaywan",
        "objective": "Notify the board.",
        "intake": {
            "channel": "email",
            "signer": "Kaywan",
            "recipient": "Board",
            "objective": "Notify the board.",
        },
    }
    selected = _select(
        {
            "assemble": _Out(payload=assemble),
            "drafter_openai": _Out(unsigned),
            "drafter_google": _Out(signed),
        }
    )
    assert selected["draft"] == signed
    assert selected["selected_role"] == "drafter_google"


@pytest.mark.parametrize(
    ("body", "expected_penalty"),
    [
        ("1. Monday unavailable\n2. Tuesday unavailable", 3),
        ("Please note the following:\nI can't cover Monday.", 3),
        ("I am responding to your request about shifts.", 3),
        ("- Monday unavailable\n- Tuesday unavailable", 3),
    ],
)
def test_correspondence_register_penalty_lists_and_throat_clearing(
    body: str, expected_penalty: int
) -> None:
    penalty = email_envelope.correspondence_register_penalty(
        body,
        channel="email",
        objective="Decline the listed dates.",
    )
    assert penalty >= expected_penalty


def test_correspondence_register_penalty_allows_numbered_list_when_objective_asks() -> (
    None
):
    body = "1. Monday unavailable\n2. Tuesday unavailable"
    penalty = email_envelope.correspondence_register_penalty(
        body,
        channel="email",
        objective="Send a numbered list of unavailable dates.",
    )
    assert penalty == 0


def test_correspondence_register_penalty_sms_formal_signoff() -> None:
    penalty = email_envelope.correspondence_register_penalty(
        "Hi Maria, I can't cover those dates. Thank you, Kaywan",
        channel="sms",
        signer="Kaywan",
        objective="Decline dates.",
    )
    assert penalty >= 3


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
