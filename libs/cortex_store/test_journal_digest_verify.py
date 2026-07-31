"""Hermetic tests for journal_digest_verify wrapper, env, and prompt binding."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from cortex_store.journal_digest_verify import (
    DIGEST_VERIFY_MODEL,
    verify_claim_batch,
)

_ENTRY_ANCHOR = "2026-07-13#wells-fargo-ploc"
_ENTRY_TEXT = (
    "Operator called Wells Fargo on 2026-07-13. A rep named Michael (?) stated "
    "the PLOC payment was 5 days overdue."
)

_BASE_CLAIM = {
    "claim": (
        "WF rep Michael (?) called 2026-07-13 stating PLOC payment 5 days overdue"
    ),
    "p_class": "P2",
    "canonicality": "assert",
    "attach_hint": "finance:wf-ploc",
    "flags": ["name_uncertain"],
    "evidence_anchor": "wells-fargo-ploc",
}

_HALLUCINATED_CLAIM = {
    "claim": "Operator received a $500 wire transfer from Chase on 2026-07-13",
    "p_class": "P1",
    "canonicality": "assert",
    "attach_hint": "finance:chase",
    "flags": [],
    "evidence_anchor": "wells-fargo-ploc",
}

_MISCLASSIFIED_CLAIM = {
    "claim": "Operator called Wells Fargo on 2026-07-13",
    "p_class": "P2",
    "canonicality": "assert",
    "attach_hint": "finance:wf-ploc",
    "flags": [],
    "evidence_anchor": "wells-fargo-ploc",
}


def _fixture_batch(*claims: dict) -> dict:
    return {
        "entry_anchor": _ENTRY_ANCHOR,
        "journal_uri": "cortex://notes/journal/2026-07-13.md",
        "claims": list(claims),
    }


ADVERSARIAL_VERIFIER_RESPONSE = json.dumps(
    [
        {
            "claim_index": 0,
            "verdict": "flag",
            "note": "hallucination: wire transfer not in source entry",
        },
        {
            "claim_index": 1,
            "verdict": "flag",
            "note": "provenance misclassification: operator-observed call is P1 not P2",
        },
    ]
)

ALL_PASS_VERIFIER_RESPONSE = json.dumps(
    [
        {"claim_index": 0, "verdict": "pass", "note": ""},
    ]
)


@pytest.mark.offline
def test_verify_claim_batch_empty_verify_model() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    with patch.dict(
        "os.environ",
        {
            "CORTEX_DIGEST_VERIFY_MODEL": "",
            "CORTEX_DIGEST_EXTRACT_MODEL": "openai/gpt-4",
        },
        clear=False,
    ):
        import cortex_store.journal_digest_verify as mod

        with patch.object(mod, "DIGEST_VERIFY_MODEL", ""):
            assert (
                verify_claim_batch(_ENTRY_TEXT, batch, entry_anchor=_ENTRY_ANCHOR)
                is None
            )


@pytest.mark.offline
def test_verify_claim_batch_same_family_skips_without_call() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    with patch.dict(
        "os.environ",
        {
            "CORTEX_DIGEST_VERIFY_MODEL": "openai/gpt-4o",
            "CORTEX_DIGEST_EXTRACT_MODEL": "openai/gpt-4",
        },
        clear=False,
    ):
        import cortex_store.journal_digest_verify as mod

        with (
            patch.object(mod, "DIGEST_VERIFY_MODEL", "openai/gpt-4o"),
            patch.object(mod, "DIGEST_EXTRACT_MODEL", "openai/gpt-4"),
            patch.object(mod, "_chat_completion") as mock_chat,
        ):
            result = verify_claim_batch(_ENTRY_TEXT, batch, entry_anchor=_ENTRY_ANCHOR)
    assert result is None
    mock_chat.assert_not_called()


@pytest.mark.offline
def test_verify_claim_batch_happy_path_all_pass() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    with patch(
        "cortex_store.journal_digest_verify._chat_completion",
        return_value=ALL_PASS_VERIFIER_RESPONSE,
    ):
        with patch.dict(
            "os.environ",
            {
                "CORTEX_DIGEST_VERIFY_MODEL": "anthropic/claude-3",
                "CORTEX_DIGEST_EXTRACT_MODEL": "openai/gpt-4",
            },
            clear=False,
        ):
            import cortex_store.journal_digest_verify as mod

            with (
                patch.object(mod, "DIGEST_VERIFY_MODEL", "anthropic/claude-3"),
                patch.object(mod, "DIGEST_EXTRACT_MODEL", "openai/gpt-4"),
            ):
                result = verify_claim_batch(
                    _ENTRY_TEXT, batch, entry_anchor=_ENTRY_ANCHOR
                )

    assert result is not None
    assert result["claims"][0]["verify_verdict"] == "pass"
    assert result["claims"][0]["claim"] == _BASE_CLAIM["claim"]
    assert result["claims"][0]["p_class"] == _BASE_CLAIM["p_class"]
    assert result["verify_verdicts"]["0"]["verdict"] == "pass"


@pytest.mark.offline
def test_adversarial_self_test_flags_hallucination_and_misclassification() -> None:
    """Impl-plan Acceptance #2: hallucinated + P1-as-P2 both surfaced."""
    batch = _fixture_batch(_HALLUCINATED_CLAIM, _MISCLASSIFIED_CLAIM)
    with patch(
        "cortex_store.journal_digest_verify._chat_completion",
        return_value=ADVERSARIAL_VERIFIER_RESPONSE,
    ):
        with patch.dict(
            "os.environ",
            {
                "CORTEX_DIGEST_VERIFY_MODEL": "anthropic/claude-3",
                "CORTEX_DIGEST_EXTRACT_MODEL": "openai/gpt-4",
            },
            clear=False,
        ):
            import cortex_store.journal_digest_verify as mod

            with (
                patch.object(mod, "DIGEST_VERIFY_MODEL", "anthropic/claude-3"),
                patch.object(mod, "DIGEST_EXTRACT_MODEL", "openai/gpt-4"),
            ):
                result = verify_claim_batch(
                    _ENTRY_TEXT, batch, entry_anchor=_ENTRY_ANCHOR
                )

    assert result is not None
    assert len(result["claims"]) == 2
    assert result["claims"][0]["verify_verdict"] == "flag"
    assert "hallucination" in result["claims"][0]["verify_note"].lower()
    assert result["claims"][1]["verify_verdict"] == "flag"
    assert "misclassification" in result["claims"][1]["verify_note"].lower()
    assert result["verify_verdicts"]["0"]["verdict"] == "flag"
    assert result["verify_verdicts"]["1"]["verdict"] == "flag"


@pytest.mark.offline
def test_env_knob_name() -> None:
    assert DIGEST_VERIFY_MODEL == __import__("os").environ.get(
        "CORTEX_DIGEST_VERIFY_MODEL", ""
    )


@pytest.mark.offline
def test_journal_prompt_uses_entry_anchor_labels() -> None:
    import cortex_store.journal_digest_verify as mod

    batch = _fixture_batch(_BASE_CLAIM)
    prompt = mod._build_journal_user_prompt(_ENTRY_TEXT, batch, _ENTRY_ANCHOR)
    assert "Entry anchor:" in prompt
    assert "SOURCE ENTRY:" in prompt
    assert "CLAIM BATCH JSON:" in prompt
    assert _ENTRY_ANCHOR in prompt
    assert _ENTRY_TEXT in prompt


@pytest.mark.offline
@pytest.mark.parametrize(
    ("duplicate_of", "expected"),
    [
        (42, True),
        (99, False),
        ("42", False),
        (True, False),
        (0, False),
        (-1, False),
    ],
)
def test_journal_wrapper_strictly_validates_duplicate_of(
    duplicate_of: object,
    expected: bool,
) -> None:
    import cortex_store.journal_digest_verify as mod

    batch = _fixture_batch(_BASE_CLAIM)
    batch["claims"][0]["dedup_candidates"] = [
        {"id": 42, "fingerprint": "abc123", "claim": _BASE_CLAIM["claim"]}
    ]
    response = json.dumps(
        [
            {
                "claim_index": 0,
                "verdict": "pass",
                "note": "",
                "duplicate_of": duplicate_of,
            }
        ]
    )
    with (
        patch.object(mod, "DIGEST_VERIFY_MODEL", "anthropic/claude-3"),
        patch.object(mod, "DIGEST_EXTRACT_MODEL", "openai/gpt-4"),
        patch.object(mod, "_chat_completion", return_value=response),
    ):
        result = verify_claim_batch(_ENTRY_TEXT, batch, entry_anchor=_ENTRY_ANCHOR)

    assert result is not None
    claim = result["claims"][0]
    assert ("duplicate_of" in claim) is expected
    assert ("dedup_candidate_fingerprint" in claim) is expected
