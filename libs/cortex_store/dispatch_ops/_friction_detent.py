"""Friction follow-on detent triage — enqueue-time aperture for conveyor.

Path-sim skill: ``|material_sub_parts| ≤ 2 ∧ loci known ∧ ¬ architecture
⇒ detent=closed`` (thin L2 + light consult; skip bundled R). Conveyor mint
must stamp this so autonomous admission does not pay full Q→R→close on every
item (dogfood 5854 cost shape).
"""

from __future__ import annotations

import re
from typing import Literal

FrictionDetent = Literal["closed", "standard", "wide"]

# Architecture-suitability / multi-rival — needs bundled arc (or wider).
# ¬ bare attr names (judgment_required) — those appear in mechanical gate bugs.
_WIDE_RE = re.compile(
    r"\b("
    r"architecture\s+suitab|"
    r"rival\s+architect|"
    r"redesign\s+(?:the\s+)?(?:architecture|substrate|protocol)|"
    r"cross[- ]agent\s+(?:scope|protocol|invariant)|"
    r"invariant[- ]touch|"
    r"substrate\s+change|"
    r"multi[- ]service\s+(?:redesign|split)|"
    r"ontology\s+(?:edit|change)|"
    r"protocol\s+split|"
    r"detent\s*=\s*frontier|"
    r"\bfrontier\s+detent\b"
    r")",
    re.IGNORECASE,
)

# Concrete loci already in the friction body / suggestion.
_LOCI_RE = re.compile(
    r"(?:"
    r"`[^`]{3,}`|"
    r"[\w./-]+\.py\b|"
    r"\b(?:libs|scripts|services|cursor-plugins)/[\w./-]+|"
    r"(?:Fix|In|pass|mirror|extract|Suggestion)\s*[:：]|"
    r"\blines?\s+\d+"
    r")",
    re.IGNORECASE,
)


def classify_friction_detent(
    *,
    claim: str = "",
    note: str = "",
    suggestion: str = "",
    category: str = "",
) -> FrictionDetent:
    """Return detent for a charter friction follow-on todo.

    - ``wide`` — architecture / rival / invariant language in the claim
    - ``closed`` — concrete loci or a non-empty suggestion (patch shape known)
    - ``standard`` — actionable but loci not pre-selected (bundled path-sim)
    """
    blob = " ".join(
        p for p in (claim, note, suggestion, category) if (p or "").strip()
    )
    if not blob.strip():
        return "standard"
    if _WIDE_RE.search(blob):
        return "wide"
    if (suggestion or "").strip():
        return "closed"
    if _LOCI_RE.search(blob):
        return "closed"
    return "standard"


__all__ = ["FrictionDetent", "classify_friction_detent"]
