"""Review dispatch contract text for API and cursor-sdk generate bodies."""

from __future__ import annotations

REVIEW_CONTRACTS = frozenset({"code-review", "check-review", "delivery-review"})

REVIEW_VERDICT_CONTRACT = (
    "REVIEW VERDICT CONTRACT: When the packet names a commit range or head, "
    "the closeout's first line is the one unfenced canonical line "
    "`VERDICT: APPROVE range=<40 lowercase hex>..<40 lowercase hex> "
    "head=<40 lowercase hex>` or the same line with `REQUEST_CHANGES`. "
    "`head` equals the range tip. `fixes=<full sha of the REQUEST_CHANGES tip>` "
    "is present only when this review closes one. There is no second "
    "`VERDICT:` line. When the packet names no commit, do not invent "
    "`range=` or `head=`. Cite findings as `path:line`."
)

REVIEW_GIT_INSPECTION_GUIDANCE = (
    "CODE REVIEW INSPECTION: Use git when available (`git log`, `git diff`, "
    "`git show`), or inspect the diff/files via `fs` or the inline diff/range "
    "provided in the prompt/packet. Do not approve from packet prose alone when "
    "a commit range is named."
)


def review_dispatch_system_appendix(resolved_contract: str | None) -> str | None:
    """Return verdict and git-inspection system appendix for code-review class contracts."""
    if (resolved_contract or "") not in REVIEW_CONTRACTS:
        return None
    return f"{REVIEW_VERDICT_CONTRACT}\n\n{REVIEW_GIT_INSPECTION_GUIDANCE}"
