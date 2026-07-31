"""Hermetic tests for claim_batch_verify family-split, parse, reconcile, adversarial."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from cortex_store.claim_batch_verify import (
    ClaimBatchVerifyConfig,
    enforce_family_split,
    parse_verifier_response,
    reconcile_batch,
    verify_claim_batch,
)
from cortex_store.journal_digest_extract import validate_claim

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

_CORRECTABLE_KEYS = frozenset(
    {"claim", "p_class", "canonicality", "attach_hint", "flags", "evidence_anchor"}
)


def _digest_pass_metadata_resolver(
    claim: dict,
    verdict_row: dict,
) -> dict:
    raw_dup = verdict_row.get("duplicate_of")
    if raw_dup is None:
        return {}
    if not isinstance(raw_dup, int) or isinstance(raw_dup, bool) or raw_dup <= 0:
        return {}

    candidates = claim.get("dedup_candidates") or []
    if not isinstance(candidates, list):
        return {}

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("id") == raw_dup:
            fingerprint = candidate.get("fingerprint")
            if isinstance(fingerprint, str) and fingerprint:
                return {
                    "duplicate_of": raw_dup,
                    "dedup_candidate_fingerprint": fingerprint,
                }
    return {}


def _digest_config() -> ClaimBatchVerifyConfig:
    return ClaimBatchVerifyConfig(
        validate_claim=validate_claim,
        correctable_claim_keys=_CORRECTABLE_KEYS,
        pass_only_keys=frozenset({"duplicate_of"}),
        pass_metadata_resolver=_digest_pass_metadata_resolver,
    )


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
def test_enforce_family_split_different_families() -> None:
    assert enforce_family_split("openai/gpt-4", "anthropic/claude-3") is True
    assert enforce_family_split("xai/grok-2", "google/gemini-pro") is True


@pytest.mark.offline
def test_enforce_family_split_same_family() -> None:
    assert enforce_family_split("openai/gpt-4", "openai/gpt-4o") is False
    assert (
        enforce_family_split("anthropic/claude-3", "anthropic/claude-sonnet") is False
    )


@pytest.mark.offline
def test_enforce_family_split_empty_model() -> None:
    assert enforce_family_split("", "anthropic/claude-3") is False
    assert enforce_family_split("openai/gpt-4", "") is False


@pytest.mark.offline
def test_verify_claim_batch_same_family_skips_without_call() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    mock_complete = __import__("unittest.mock").mock.MagicMock()
    result = verify_claim_batch(
        _ENTRY_TEXT,
        batch,
        source_anchor=_ENTRY_ANCHOR,
        extract_model="openai/gpt-4",
        verify_model="openai/gpt-4o",
        system_prompt="system",
        complete=mock_complete,
        config=_digest_config(),
    )
    assert result is None
    mock_complete.assert_not_called()


@pytest.mark.offline
def test_verify_claim_batch_happy_path_all_pass() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    mock_complete = __import__("unittest.mock").mock.MagicMock(
        return_value=ALL_PASS_VERIFIER_RESPONSE
    )
    result = verify_claim_batch(
        _ENTRY_TEXT,
        batch,
        source_anchor=_ENTRY_ANCHOR,
        extract_model="openai/gpt-4",
        verify_model="anthropic/claude-3",
        system_prompt="system",
        complete=mock_complete,
        config=_digest_config(),
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
    mock_complete = __import__("unittest.mock").mock.MagicMock(
        return_value=ADVERSARIAL_VERIFIER_RESPONSE
    )
    result = verify_claim_batch(
        _ENTRY_TEXT,
        batch,
        source_anchor=_ENTRY_ANCHOR,
        extract_model="openai/gpt-4",
        verify_model="anthropic/claude-3",
        system_prompt="system",
        complete=mock_complete,
        config=_digest_config(),
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
def test_reconcile_correct_applies_valid_fields() -> None:
    batch = _fixture_batch(_MISCLASSIFIED_CLAIM)
    verdict_rows = [
        {
            "claim_index": 0,
            "verdict": "correct",
            "note": "reclassified P2 to P1",
            "p_class": "P1",
        }
    ]
    result = reconcile_batch(batch, verdict_rows, _digest_config())
    assert result["claims"][0]["verify_verdict"] == "correct"
    assert result["claims"][0]["p_class"] == "P1"
    assert result["claims"][0]["claim"] == _MISCLASSIFIED_CLAIM["claim"]


@pytest.mark.offline
def test_reconcile_invalid_correct_becomes_flag() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    verdict_rows = [
        {
            "claim_index": 0,
            "verdict": "correct",
            "note": "bad p_class",
            "p_class": "P9",
        }
    ]
    result = reconcile_batch(batch, verdict_rows, _digest_config())
    assert result["claims"][0]["verify_verdict"] == "flag"
    assert result["claims"][0]["verify_note"] == "invalid_verifier_output"


@pytest.mark.offline
def test_reconcile_duplicate_of_on_pass() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    batch["claims"][0]["dedup_candidates"] = [
        {"id": 42, "fingerprint": "abc123", "claim": _BASE_CLAIM["claim"]}
    ]
    verdict_rows = [
        {"claim_index": 0, "verdict": "pass", "note": "", "duplicate_of": 42}
    ]
    result = reconcile_batch(batch, verdict_rows, _digest_config())
    assert result["claims"][0]["duplicate_of"] == 42
    assert result["claims"][0]["dedup_candidate_fingerprint"] == "abc123"


@pytest.mark.offline
def test_parse_verifier_response_preserves_duplicate_of_on_pass() -> None:
    rows = parse_verifier_response(
        json.dumps(
            [
                {
                    "claim_index": 0,
                    "verdict": "pass",
                    "note": "",
                    "duplicate_of": 42,
                }
            ]
        ),
        claim_count=1,
        config=_digest_config(),
    )

    assert rows is not None
    assert rows[0]["duplicate_of"] == 42


@pytest.mark.offline
def test_reconcile_fabricated_duplicate_of_ignored() -> None:
    batch = _fixture_batch(_BASE_CLAIM)
    batch["claims"][0]["dedup_candidates"] = [
        {"id": 42, "fingerprint": "abc123", "claim": _BASE_CLAIM["claim"]}
    ]
    verdict_rows = [
        {"claim_index": 0, "verdict": "pass", "note": "", "duplicate_of": 99}
    ]
    result = reconcile_batch(batch, verdict_rows, _digest_config())
    assert "duplicate_of" not in result["claims"][0]


@pytest.mark.offline
def test_reconcile_duplicate_of_ignored_on_correct() -> None:
    batch = _fixture_batch(_MISCLASSIFIED_CLAIM)
    batch["claims"][0]["dedup_candidates"] = [
        {"id": 42, "fingerprint": "abc123", "claim": _MISCLASSIFIED_CLAIM["claim"]}
    ]
    verdict_rows = [
        {
            "claim_index": 0,
            "verdict": "correct",
            "note": "reclassified P2 to P1",
            "p_class": "P1",
            "duplicate_of": 42,
        }
    ]
    result = reconcile_batch(batch, verdict_rows, _digest_config())
    assert result["claims"][0]["verify_verdict"] == "correct"
    assert "duplicate_of" not in result["claims"][0]


@pytest.mark.offline
def test_parse_verifier_response_malformed_json() -> None:
    rows = parse_verifier_response(
        "not json",
        claim_count=1,
        config=_digest_config(),
    )
    assert rows is None


@pytest.mark.offline
def test_parse_verifier_response_missing_row_filled() -> None:
    rows = parse_verifier_response(
        json.dumps([]),
        claim_count=2,
        config=_digest_config(),
    )
    assert rows is not None
    assert len(rows) == 2
    assert rows[0]["verdict"] == "flag"
    assert rows[1]["verdict"] == "flag"


@pytest.mark.offline
def test_module_has_no_forbidden_imports() -> None:
    source = (
        Path(__file__).resolve().parent.joinpath("claim_batch_verify.py").read_text()
    )
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    forbidden = {
        "journal_digest",
        "httpx",
        "os",
        "cortex_conn",
        "events_",
    }
    for name in imported:
        for forbidden_prefix in forbidden:
            assert not name.startswith(forbidden_prefix), (
                f"forbidden import {name!r} in claim_batch_verify.py"
            )

    assert "os.environ" not in source
    assert "STARGATE" not in source
