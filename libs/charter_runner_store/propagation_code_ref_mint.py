"""Mint-time ``code_ref`` resolve — refuse unsatisfiable tokens before insert.

STATUS_CLAIM_KIND on the ledger column is ``observed_of_attempt``: an open row
is one obligation *attempt*. Minting a token that can never meet proof creates
a false open, not a real pending-proof debt. Reject at the chokepoint.
"""

from __future__ import annotations

import re

from deploy_identity import code_ref_relation as code_ref_relation_mod
from deploy_identity.code_version import normalize_code_ref
from implement_admission.propagation_row import PropagationRow

# Failure reason tokens (attempt outcomes — not standing fleet debt).
REASON_UNSATISFIABLE_CODE_REF = "unsatisfiable_code_ref"
REASON_UNSATISFIABLE_DEPLOY_LINE = "unsatisfiable_deploy_line"

_HEX_TOKEN_RE = re.compile(r"\b([0-9a-f]{7,40})\b", re.IGNORECASE)

_ADMIT_FIX = (
    "Pass a resolvable git commit (full 40-char SHA or unambiguous short SHA), "
    "or HEAD. Prose tokens like 'working' and mangled/non-object hex are refused."
)


class UnresolvableCodeRefError(ValueError):
    """Raised when mint receives a ``code_ref`` that is not a git commit object."""

    def __init__(self, code_ref: str, *, service: str | None = None) -> None:
        self.code_ref = code_ref
        self.service = service
        where = f" for service={service!r}" if service else ""
        super().__init__(
            f"code_ref {code_ref!r} does not resolve to a git commit object{where}. "
            f"{_ADMIT_FIX}"
        )


def admit_error_for_unresolvable_code_ref(code_ref: str) -> dict[str, str]:
    """Structured admit rejection a seat can act on (propagate contract)."""
    return {
        "reason": "code_ref_unresolvable",
        "summary": (
            f"code_ref {code_ref!r} does not resolve to a git commit object. "
            f"{_ADMIT_FIX}"
        ),
        "fix_hint": _ADMIT_FIX,
        "code_ref": code_ref,
    }


def require_resolvable_code_ref(code_ref: str, *, service: str | None = None) -> str:
    """Normalize then resolve; return concrete SHA or raise."""
    normalized = normalize_code_ref(code_ref)
    resolved = code_ref_relation_mod.resolve_commit_sha(normalized)
    if resolved is None:
        raise UnresolvableCodeRefError(normalized, service=service)
    return resolved


def mint_row_with_resolved_code_ref(row: PropagationRow) -> PropagationRow:
    """Return a row whose ``code_ref`` is a resolved commit SHA, or raise."""
    resolved = require_resolvable_code_ref(row.code_ref, service=row.service)
    if resolved == row.code_ref:
        return row
    return row.model_copy(update={"code_ref": resolved})


def try_recover_code_ref(stored: str) -> str | None:
    """Best-effort recover a resolvable SHA from an unsatisfiable stored token.

    Tries the token as-is, unique short-hex prefixes, then hex substrings in prose.
    """
    raw = str(stored or "").strip()
    if not raw:
        return None
    resolve = code_ref_relation_mod.resolve_commit_sha
    direct = resolve(raw)
    if direct is not None:
        return direct
    hexish = raw.lower()
    if 7 <= len(hexish) <= 40 and all(char in "0123456789abcdef" for char in hexish):
        for length in range(min(len(hexish), 16), 6, -1):
            recovered = resolve(hexish[:length])
            if recovered is not None:
                return recovered
    for match in _HEX_TOKEN_RE.finditer(raw):
        recovered = resolve(match.group(1))
        if recovered is not None:
            return recovered
    return None


__all__ = [
    "REASON_UNSATISFIABLE_CODE_REF",
    "REASON_UNSATISFIABLE_DEPLOY_LINE",
    "UnresolvableCodeRefError",
    "admit_error_for_unresolvable_code_ref",
    "mint_row_with_resolved_code_ref",
    "require_resolvable_code_ref",
    "try_recover_code_ref",
]
