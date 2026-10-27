"""Correspondence envelope units that are not factual claims (merge-base behavior)."""

from __future__ import annotations

import re

from writing_intake.claim_gates import check_class_for_channel

_VALEDICTION_LINE_RE = re.compile(
    r"^\s*(?:thank you|thanks|sincerely|regards|best regards|kind regards|respectfully)[,!]?\s*$",
    re.IGNORECASE,
)


def is_non_claim_envelope_unit(
    unit: str,
    *,
    channel: str | None = None,
    signer: str | None = None,
) -> bool:
    """True when a sentence block is correspondence envelope only, not a factual claim."""
    if not unit or not unit.strip():
        return True
    if check_class_for_channel(str(channel or "filing")) != "correspondence":
        return False
    non_empty = [ln.strip() for ln in unit.splitlines() if ln.strip()]
    if len(non_empty) == 1:
        first = non_empty[0]
        if re.match(
            r"^\s*(?:attached are(?:\s+the\s+following(?:\s+exhibits|\s+documents)?)?|"
            r"the\s+(?:attached\s+)?exhibits\s+are(?:\s+as\s+follows)?)\s*[:.]?\s*$",
            first,
            re.I,
        ):
            return True
    if not non_empty:
        return True
    if _VALEDICTION_LINE_RE.match(non_empty[0]):
        if len(non_empty) == 1:
            return True
        if len(non_empty) == 2:
            second = non_empty[1]
            if re.search(r"[\d.!?;:,]", second) is not None:
                return False
            words = second.split()
            if not (1 <= len(words) <= 5):
                return False
            if not all(w[0].isupper() for w in words if w):
                return False
            if signer and signer.strip():
                signer_norm = signer.strip().lower()
                second_norm = second.lower()
                tokens = signer.strip().split()
                short_name = (
                    f"{tokens[0]} {tokens[-1]}".lower()
                    if len(tokens) >= 2
                    else signer_norm
                )
                if second_norm not in {signer_norm, short_name}:
                    return False
            return True
        return False
    return False


_is_non_claim_envelope_unit = is_non_claim_envelope_unit
