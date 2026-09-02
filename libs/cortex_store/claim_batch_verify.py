"""Generic claim-batch verifier kernel — identity independence, parse, reconcile."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger("cortex-api.claim_batch_verify")

ClaimValidator = Callable[[Any], dict[str, Any] | None]
PassMetadataResolver = Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]]
CompletionFn = Callable[[str, str], str | None]
ResponseCleaner = Callable[[str], str]
UserPromptBuilder = Callable[[str, dict[str, Any], str], str]

_VALID_VERDICTS = frozenset({"pass", "correct", "flag"})


@dataclass(frozen=True)
class ClaimBatchVerifyConfig:
    validate_claim: ClaimValidator
    correctable_claim_keys: frozenset[str]
    pass_only_keys: frozenset[str] = frozenset()
    pass_metadata_resolver: PassMetadataResolver | None = None


def _strip_json_fences(text: str) -> str:
    """Remove markdown code fences from model output."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.split("\n")
        lines = [line for line in lines if not line.strip().startswith("```")]
        cleaned = "\n".join(lines).strip()
    return cleaned


def _default_build_user_prompt(
    source_text: str,
    claim_batch: dict[str, Any],
    source_anchor: str,
) -> str:
    return (
        f"Source anchor: {source_anchor}\n\n"
        f"SOURCE TEXT:\n{source_text}\n\n"
        f"CLAIM BATCH JSON:\n{json.dumps(claim_batch, indent=2)}"
    )


def _normalize_verdict_row(
    raw: Any,
    claim_index: int,
    config: ClaimBatchVerifyConfig,
) -> dict[str, Any]:
    """Return a normalized verdict row or a flag stub for invalid output."""
    if not isinstance(raw, dict):
        return {
            "claim_index": claim_index,
            "verdict": "flag",
            "note": "invalid_verifier_output",
        }

    verdict = raw.get("verdict")
    if verdict not in _VALID_VERDICTS:
        return {
            "claim_index": claim_index,
            "verdict": "flag",
            "note": "invalid_verifier_output",
        }

    note = raw.get("note", "")
    if not isinstance(note, str):
        note = str(note)

    if verdict in {"correct", "flag"} and not note.strip():
        return {
            "claim_index": claim_index,
            "verdict": "flag",
            "note": "invalid_verifier_output",
        }

    row: dict[str, Any] = {
        "claim_index": claim_index,
        "verdict": verdict,
        "note": note.strip(),
    }
    for key in config.correctable_claim_keys:
        if key in raw:
            row[key] = raw[key]
    if verdict == "pass":
        for key in config.pass_only_keys:
            if key in raw:
                row[key] = raw[key]
    return row


def parse_verifier_response(
    raw_text: str,
    *,
    claim_count: int,
    config: ClaimBatchVerifyConfig,
    response_cleaner: ResponseCleaner | None = None,
) -> list[dict[str, Any]] | None:
    """Parse verifier JSON array; fill gaps with invalid_verifier_output flags."""
    cleaner = response_cleaner or _strip_json_fences
    cleaned = cleaner(raw_text)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.warning("Verifier returned invalid JSON: %.200s", cleaned)
        return None

    if not isinstance(parsed, list):
        logger.warning("Verifier returned non-array: %s", type(parsed))
        return None

    by_index: dict[int, dict[str, Any]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("claim_index")
        if not isinstance(idx, int) or idx < 0 or idx >= claim_count:
            continue
        by_index[idx] = _normalize_verdict_row(item, idx, config)

    verdicts: list[dict[str, Any]] = []
    for i in range(claim_count):
        if i in by_index:
            verdicts.append(by_index[i])
        else:
            verdicts.append(
                {
                    "claim_index": i,
                    "verdict": "flag",
                    "note": "invalid_verifier_output",
                }
            )
    return verdicts


def _apply_correction(
    claim: dict[str, Any],
    verdict_row: dict[str, Any],
    config: ClaimBatchVerifyConfig,
) -> dict[str, Any]:
    """Apply verifier corrections when schema-valid; else flag."""
    candidate = dict(claim)
    for key in config.correctable_claim_keys:
        if key in verdict_row:
            candidate[key] = verdict_row[key]

    validated = config.validate_claim(candidate)
    if validated is None:
        flagged = dict(claim)
        flagged["verify_verdict"] = "flag"
        flagged["verify_note"] = "invalid_verifier_output"
        if "invalid_verifier_output" not in flagged.get("flags", []):
            flagged["flags"] = [*flagged.get("flags", []), "invalid_verifier_output"]
        return flagged

    validated["verify_verdict"] = "correct"
    validated["verify_note"] = verdict_row.get("note", "")
    return validated


def _reconcile_claim(
    claim: dict[str, Any],
    verdict_row: dict[str, Any],
    config: ClaimBatchVerifyConfig,
) -> dict[str, Any]:
    verdict = verdict_row["verdict"]
    if verdict == "pass":
        reconciled = dict(claim)
        reconciled["verify_verdict"] = "pass"
        reconciled["verify_note"] = verdict_row.get("note", "")
        if config.pass_metadata_resolver is not None:
            reconciled.update(config.pass_metadata_resolver(claim, verdict_row))
        return reconciled

    if verdict == "correct":
        return _apply_correction(claim, verdict_row, config)

    flagged = dict(claim)
    flagged["verify_verdict"] = "flag"
    note = verdict_row.get("note", "invalid_verifier_output")
    flagged["verify_note"] = note
    if note and note not in flagged.get("flags", []):
        flagged["flags"] = [*flagged.get("flags", []), note]
    return flagged


def reconcile_batch(
    claim_batch: dict[str, Any],
    verdict_rows: list[dict[str, Any]],
    config: ClaimBatchVerifyConfig,
) -> dict[str, Any]:
    """Merge verifier verdicts into claim batch; never drop claims."""
    claims = claim_batch.get("claims", [])
    reconciled_claims = [
        _reconcile_claim(claim, verdict_rows[i], config)
        for i, claim in enumerate(claims)
    ]

    verify_verdicts = {
        str(i): {
            "verdict": row["verify_verdict"],
            "note": row.get("verify_note", ""),
        }
        for i, row in enumerate(reconciled_claims)
    }

    out = dict(claim_batch)
    out["claims"] = reconciled_claims
    out["verify_verdicts"] = verify_verdicts
    return out


def verify_claim_batch(
    source_text: str,
    claim_batch: dict[str, Any],
    *,
    source_anchor: str,
    extract_model: str,
    verify_model: str,
    system_prompt: str,
    complete: CompletionFn,
    config: ClaimBatchVerifyConfig,
    response_cleaner: ResponseCleaner | None = None,
    build_user_prompt: UserPromptBuilder | None = None,
) -> dict[str, Any] | None:
    """Return reconciled batch with per-claim verdicts, or None on skip/failure."""
    if not verify_model or not extract_model:
        logger.warning(
            "Claim batch verify skipped: extract or verify model not configured"
        )
        return None

    from implement_admission.check_review_substrate import (
        consultant_identity,
        independently_measured,
    )

    if not independently_measured(
        consultant_identity(extract_model, None),
        consultant_identity(verify_model, None),
    ):
        logger.error(
            "Claim batch verify rejected: extract model %r and verify model %r "
            "must be independently measured (different identity or effort rung)",
            extract_model,
            verify_model,
        )
        return None

    claims = claim_batch.get("claims")
    if not isinstance(claims, list) or not claims:
        logger.warning("Claim batch verify skipped: empty or invalid claims array")
        return None

    builder = build_user_prompt or _default_build_user_prompt
    user_prompt = builder(source_text, claim_batch, source_anchor)
    result = complete(system_prompt, user_prompt)
    if result is None:
        return None

    verdict_rows = parse_verifier_response(
        result,
        claim_count=len(claims),
        config=config,
        response_cleaner=response_cleaner,
    )
    if verdict_rows is None:
        return None

    return reconcile_batch(claim_batch, verdict_rows, config)
