"""Claim normalization and hashing for assertion dedup.

Normalization is versioned — any change to the normalization logic
must bump CLAIM_NORM_VERSION and trigger a bulk rehash migration.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata

CLAIM_NORM_VERSION = 1

_TRAILING_PUNCT = re.compile(r"[.!?,;:]+$")
_WHITESPACE_RUNS = re.compile(r"\s+")


def normalize_claim(claim: str) -> str:
    """Normalize claim text for dedup hashing.

    Steps: lowercase → strip → collapse whitespace → NFC → strip trailing punct.
    """
    text = claim.lower().strip()
    text = _WHITESPACE_RUNS.sub(" ", text)
    text = unicodedata.normalize("NFC", text)
    text = _TRAILING_PUNCT.sub("", text)
    return text


def compute_claim_hash(entity_id: str, claim: str) -> str:
    """SHA-256 hash scoped to entity — same claim on different entities is allowed."""
    normalized = normalize_claim(claim)
    return hashlib.sha256(f"{entity_id}:{normalized}".encode()).hexdigest()
