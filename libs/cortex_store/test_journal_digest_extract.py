"""Hermetic tests for journal_digest_extract parser, validator, and extract_claims."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cortex_store.journal_digest_extract import (
    DIGEST_EXTRACT_MODEL,
    extract_claims,
    parse_claim_batch,
    strip_json_fences,
    validate_claim,
)

_ENTRY_ANCHOR = "2026-07-13#wells-fargo-ploc"
_JOURNAL_URI = "cortex://notes/journal/2026-07-13.md"


FIXTURE_P2_SQUARED = json.dumps(
    {
        "claims": [
            {
                "claim": (
                    "Carol stated that she told Lindsey she was indisposed due to "
                    "pneumonia, characterizing it herself as an excuse/white lie."
                ),
                "p_class": "P2²",
                "canonicality": "assert",
                "attach_hint": "person:carol-bowman",
                "flags": [],
                "evidence_anchor": "carol-bowman",
            }
        ]
    }
)

FIXTURE_NAME_UNCERTAIN = json.dumps(
    {
        "claims": [
            {
                "claim": (
                    'WF rep Michael (?) called 2026-07-13 stating PLOC payment '
                    "5 days overdue"
                ),
                "p_class": "P2",
                "canonicality": "assert",
                "attach_hint": "finance:wf-ploc",
                "flags": ["name_uncertain"],
                "evidence_anchor": "wells-fargo-ploc",
            }
        ]
    }
)

FIXTURE_DEADLINE_CONFLICT = json.dumps(
    {
        "claims": [
            {
                "claim": (
                    "Operator received 15-day cancellation notice: $786.71 by "
                    "2026-07-22 to stop service interruption"
                ),
                "p_class": "P1",
                "canonicality": "assert",
                "attach_hint": "case:pge-gas-backbilling-dispute-2026",
                "flags": [],
                "evidence_anchor": "pge-backbilling",
            },
            {
                "claim": (
                    "Marlena stated the notice can be disregarded and verbally "
                    "quoted a higher figure she believes ensures continuation"
                ),
                "p_class": "P2",
                "canonicality": "assert",
                "attach_hint": "case:pge-gas-backbilling-dispute-2026",
                "flags": ["deadline_conflict"],
                "evidence_anchor": "pge-backbilling",
            },
        ]
    }
)


@pytest.mark.offline
def test_strip_json_fences() -> None:
    wrapped = '```json\n{"claims": []}\n```'
    assert strip_json_fences(wrapped) == '{"claims": []}'


@pytest.mark.offline
def test_validate_claim_rejects_missing_keys() -> None:
    assert validate_claim({"claim": "x"}) is None


@pytest.mark.offline
def test_validate_claim_rejects_bad_p_class() -> None:
    row = {
        "claim": "Operator called X",
        "p_class": "P4",
        "canonicality": "assert",
        "attach_hint": None,
        "flags": [],
        "evidence_anchor": "section",
    }
    assert validate_claim(row) is None


@pytest.mark.offline
@pytest.mark.parametrize(
    ("fixture", "expected_p_class", "expected_flags"),
    [
        (FIXTURE_P2_SQUARED, "P2²", []),
        (FIXTURE_NAME_UNCERTAIN, "P2", ["name_uncertain"]),
        (FIXTURE_DEADLINE_CONFLICT, "P2", ["deadline_conflict"]),
    ],
)
def test_parse_claim_batch_fixtures(
    fixture: str,
    expected_p_class: str,
    expected_flags: list[str],
) -> None:
    batch = parse_claim_batch(
        fixture,
        entry_anchor=_ENTRY_ANCHOR,
        journal_uri=_JOURNAL_URI,
    )
    assert batch is not None
    assert batch["entry_anchor"] == _ENTRY_ANCHOR
    assert batch["journal_uri"] == _JOURNAL_URI
    matching = [c for c in batch["claims"] if c["p_class"] == expected_p_class]
    assert matching, f"no claim with p_class={expected_p_class}"
    assert matching[0]["flags"] == expected_flags


@pytest.mark.offline
def test_parse_claim_batch_rejects_invalid_rows_with_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = json.dumps(
        {
            "claims": [
                {
                    "claim": "Valid claim",
                    "p_class": "P1",
                    "canonicality": "prose",
                    "attach_hint": None,
                    "flags": [],
                    "evidence_anchor": "health",
                },
                {"claim": "missing fields"},
            ]
        }
    )
    batch = parse_claim_batch(
        payload,
        entry_anchor=_ENTRY_ANCHOR,
        journal_uri=_JOURNAL_URI,
    )
    assert batch is not None
    assert len(batch["claims"]) == 1
    assert any("rejected 1 invalid" in rec.message for rec in caplog.records)


@pytest.mark.offline
def test_parse_claim_batch_fenced_json() -> None:
    fenced = f"```json\n{FIXTURE_NAME_UNCERTAIN}\n```"
    batch = parse_claim_batch(
        fenced,
        entry_anchor=_ENTRY_ANCHOR,
        journal_uri=_JOURNAL_URI,
    )
    assert batch is not None
    assert batch["claims"][0]["flags"] == ["name_uncertain"]


@pytest.mark.offline
def test_extract_claims_returns_none_when_model_unconfigured() -> None:
    with patch.dict("os.environ", {"CORTEX_DIGEST_EXTRACT_MODEL": ""}, clear=False):
        import cortex_store.journal_digest_extract as mod

        with patch.object(mod, "DIGEST_EXTRACT_MODEL", ""):
            assert (
                extract_claims(
                    "entry body",
                    entry_anchor=_ENTRY_ANCHOR,
                    journal_uri=_JOURNAL_URI,
                )
                is None
            )


@pytest.mark.offline
def test_extract_claims_with_mocked_completion() -> None:
    with patch(
        "cortex_store.journal_digest_extract._chat_completion",
        return_value=FIXTURE_P2_SQUARED,
    ):
        batch = extract_claims(
            "Carol told Lindsey she had pneumonia...",
            entry_anchor="2026-07-13#carol-bowman",
            journal_uri=_JOURNAL_URI,
        )
    assert batch is not None
    assert batch["claims"][0]["p_class"] == "P2²"


@pytest.mark.offline
def test_env_knob_name() -> None:
    assert DIGEST_EXTRACT_MODEL == __import__("os").environ.get(
        "CORTEX_DIGEST_EXTRACT_MODEL", ""
    )
