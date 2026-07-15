"""Journal digest VERIFY — adversarial cross-family claim review and reconcile.

Family-split rule: model family is the provider token before the first ``/``
(e.g. ``openai/gpt-4`` → ``openai``, ``anthropic/claude-3`` → ``anthropic``).
Extract and verify models must be non-empty and from different families; same
family is rejected with ERROR and no LLM call.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
from universal_logging import get_logger

from cortex_store.claim_batch_verify import (
    ClaimBatchVerifyConfig,
)
from cortex_store.claim_batch_verify import (
    verify_claim_batch as _verify_claim_batch,
)
from cortex_store.journal_digest_extract import validate_claim

logger = get_logger("cortex-api.journal_digest_verify")

DIGEST_VERIFY_MODEL = os.environ.get("CORTEX_DIGEST_VERIFY_MODEL", "")
DIGEST_EXTRACT_MODEL = os.environ.get("CORTEX_DIGEST_EXTRACT_MODEL", "")

STARGATE_URL = "http://localhost:9999"

_CORRECTABLE_CLAIM_KEYS = frozenset(
    {"claim", "p_class", "canonicality", "attach_hint", "flags", "evidence_anchor"}
)

_VERIFY_SYSTEM = """\
You are an adversarial reviewer of journal-digest claim batches.

Given a SOURCE ENTRY and a CLAIM BATCH JSON, check each claim for:
- hallucination (not grounded in the source entry)
- provenance misclassification (P1 vs P2 vs P2² vs P3)
- wrong entity attachment hint
- collapsed never-collapse cases (e.g. P2 figure merged with P1 dispute)
- smoothed ambiguity (uncertainty markers or garbled phrasing repaired)
- verbal-over-written deadline errors (written deadline downgraded by verbal quote)

Verdict per claim: pass | correct | flag
- pass: claim is accurate and well-classified
- correct: fixable error — include corrected fields (claim, p_class, flags, etc.)
- flag: serious divergence — short note required

Return a JSON array; one object per claim in batch order (claim_index 0-based):
- claim_index (int)
- verdict (str): pass | correct | flag
- note (str): required for correct and flag; empty string allowed for pass
- optional corrected fields on correct: claim, p_class, canonicality, attach_hint,
  flags, evidence_anchor
- optional on pass only: duplicate_of (int) — assertion id from that claim's
  dedup_candidates when one candidate fully entails every material fact in the
  atomic digest claim. Different amount, date/temporal scope, polarity,
  attribution/provenance, uncertainty, or source identity forbids duplicate_of.
  Omit when no safe match.

Each claim in the batch includes dedup_candidates (may be empty): prior same-source
assertions eligible for semantic dedup with id, entity_id, claim, derivation_type,
evidence_uris, valid_from, valid_until, fingerprint.

Return ONLY valid JSON. No markdown fences, no commentary."""


def _max_tokens() -> int:
    return int(os.environ.get("CORTEX_DIGEST_MAX_TOKENS", "16384"))


def _request_timeout() -> float:
    return float(os.environ.get("CORTEX_DIGEST_TIMEOUT_S", "180"))


def _get_verify_model() -> str:
    if DIGEST_VERIFY_MODEL:
        return DIGEST_VERIFY_MODEL
    return ""


def _chat_completion(system: str, user: str) -> str | None:
    """Call Stargate chat completions for verify. Returns content or None."""
    model = _get_verify_model()
    if not model:
        logger.warning(
            "No digest verify model configured (CORTEX_DIGEST_VERIFY_MODEL empty)"
        )
        return None

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": _max_tokens(),
        "temperature": 0.2,
    }

    try:
        with httpx.Client(timeout=_request_timeout()) as client:
            resp = client.post(
                f"{STARGATE_URL}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"].strip()
    except Exception:
        logger.warning("Digest verify LLM call failed", exc_info=True)
        return None


def _digest_pass_metadata_resolver(
    claim: dict[str, Any],
    verdict_row: dict[str, Any],
) -> dict[str, Any]:
    """Accept duplicate_of only on pass when the id was offered for this claim."""
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


_DIGEST_CONFIG = ClaimBatchVerifyConfig(
    validate_claim=validate_claim,
    correctable_claim_keys=_CORRECTABLE_CLAIM_KEYS,
    pass_only_keys=frozenset({"duplicate_of"}),
    pass_metadata_resolver=_digest_pass_metadata_resolver,
)


def _build_journal_user_prompt(
    entry_text: str,
    claim_batch: dict[str, Any],
    entry_anchor: str,
) -> str:
    return (
        f"Entry anchor: {entry_anchor}\n\n"
        f"SOURCE ENTRY:\n{entry_text}\n\n"
        f"CLAIM BATCH JSON:\n{json.dumps(claim_batch, indent=2)}"
    )


def verify_claim_batch(
    entry_text: str,
    claim_batch: dict[str, Any],
    *,
    entry_anchor: str,
) -> dict[str, Any] | None:
    """Return reconciled batch with per-claim verdicts, or None on skip/failure."""
    verify_model = _get_verify_model()
    if not verify_model:
        return None

    extract_model = DIGEST_EXTRACT_MODEL
    if not extract_model or not verify_model:
        logger.warning("Digest verify skipped: extract or verify model not configured")
        return None

    return _verify_claim_batch(
        entry_text,
        claim_batch,
        source_anchor=entry_anchor,
        extract_model=extract_model,
        verify_model=verify_model,
        system_prompt=_VERIFY_SYSTEM,
        complete=_chat_completion,
        config=_DIGEST_CONFIG,
        build_user_prompt=_build_journal_user_prompt,
    )
