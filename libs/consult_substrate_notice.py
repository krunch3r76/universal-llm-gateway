"""Standing libs/ and pipeline notice for consult seats.

Consult and sketch prompts, and CDP ask/review generates, tell the seat that
``libs/`` primitives are often the right tool and that pipelines stay in scope.
The line is stamped on the prompt the seat receives, after prompt-expand when
that rewrite runs. Conductor Play does not enter prompt-expand
(``implement`` skip) and is outside this warrant.
"""

from __future__ import annotations

SUBSTRATE_NOTICE = (
    "Primitives in libs/ are often the right tool, and a missing primitive "
    "may be added there. Pipelines are always in scope: prefer an existing "
    "pipeline, and name a new one when that is the shape."
)

_MARKER = "Primitives in libs/ are often the right tool"
_CONTRACTS = frozenset({"consult", "sketch"})
_CDP_PURPOSES = frozenset({"ask", "review"})


def substrate_notice_warrants(
    *,
    contract: str | None = None,
    purpose: str | None = None,
) -> bool:
    """True for consult/sketch contracts and for CDP ask/review purposes."""
    kind = (contract or "").strip().lower()
    if kind in _CONTRACTS:
        return True
    return (purpose or "").strip().lower() in _CDP_PURPOSES


def ensure_substrate_notice(text: str) -> str:
    """Append the notice once. Idempotent when the marker is already present."""
    if _MARKER in (text or ""):
        return text
    body = text or ""
    if body and not body.endswith("\n"):
        body += "\n"
    return f"{body}\n{SUBSTRATE_NOTICE}\n"


def apply_substrate_notice_if_warranted(
    prompt: str,
    *contracts: str | None,
    purpose: str | None = None,
) -> str:
    """Stamp when any contract or the CDP purpose warrants the notice."""
    warranted = substrate_notice_warrants(purpose=purpose) or any(
        substrate_notice_warrants(contract=contract) for contract in contracts
    )
    if not warranted:
        return prompt
    return ensure_substrate_notice(prompt)


__all__ = [
    "SUBSTRATE_NOTICE",
    "apply_substrate_notice_if_warranted",
    "ensure_substrate_notice",
    "substrate_notice_warrants",
]
