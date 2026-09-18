#!/usr/bin/env python
"""Filing-fitness gate — stage 2 of the specimen selection process.

Stage 1 (``induction_trial_floor_gate.py``) checks validity and an independent
scorer ranks the specimens blind on writing quality alone. Neither stage knows
anything about whether a letter may actually be filed, and that separation is
deliberate: folding filing constraints into the blind rubric would contaminate
the elicitation measurement the trial exists to produce.

This gate runs afterwards, over the ranked list, and answers a different
question — may this specimen be filed as written? It encodes the falsifier set
verified on ``a:34202`` and the audience bind on ``a:34115``, plus the operator's
2026-09-16 ruling recorded on ``a:35210``.

A specimen that fails here is not a bad letter; it is a letter that needs a
named repair before it can go to the portal.
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LETTERS = REPO / "tmp/induction-trial-01-letters"

CHAR_CEILING = 949


@dataclass(frozen=True)
class Falsifier:
    """One forbidden move, with the assertion that forbids it."""

    code: str
    basis: str
    why: str
    patterns: tuple[str, ...]

    def hits(self, text: str) -> list[str]:
        found = []
        for pattern in self.patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                found.append(match.group(0).strip())
        return found


# Ordered worst-first: an audience-bind breach is disqualifying in a way that a
# stylistic slip is not.
FALSIFIERS: tuple[Falsifier, ...] = (
    Falsifier(
        code="audience_bind",
        basis="a:34115",
        why="identifies the employer or workplace; the filed letter may say only "
            "that the writer was working nearby",
        patterns=(
            r"\bwalgreens\b",
            r"\bpharmac(?:y|ist)\b",
            r"\bstore\s*(?:number|#)?\s*\d*\b",
            r"\bsole[- ]pharmacist\b",
            r"\bshift[- ]coverage\b",
            r"\bmy (?:shift|store|job|employer)\b",
            r"\bmy work\b",
            r"\btechnician\b",
        ),
    ),
    Falsifier(
        code="signage_argument",
        basis="a:34002",
        why="argues the signage was unclear, missing, or obscured",
        patterns=(
            r"\bsign(?:age|s)?\b[^.]{0,60}\b(?:unclear|confus\w+|missing|obscur\w+|"
            r"hidden|hard to see|not visible|faded)\b",
            r"\b(?:did ?n[o']t|could ?n[o']t)\s+(?:see|find|notice)\b[^.]{0,30}\bsign",
        ),
    ),
    Falsifier(
        code="payment_theory",
        basis="a:34032",
        why="raises gate, payment, validation, or a specific fine amount",
        patterns=(
            r"\bvalidat\w+\b",
            r"\bgate\b",
            r"\$\s?\d+",
            r"\b(?:12|10)\s?dollars?\b",
            r"\bmaximum\b[^.]{0,30}\b(?:fee|fine|charge)\b",
        ),
    ),
    Falsifier(
        code="continuous_stay",
        basis="a:31629",
        why="advances the continuous-stay theory or argues the second citation "
            "came too soon after the first",
        patterns=(
            r"\bcontinuous\b",
            r"\bone (?:single )?(?:continuous )?stay\b",
            r"\bsame (?:parking )?(?:session|stay)\b",
            r"\btoo soon after\b",
            r"\bdouble[- ]?(?:ticket|cited|citation)\w*\b",
        ),
    ),
    Falsifier(
        code="signature_block",
        basis="a:35210",
        why="carries a signature block; the portal form already captures identity",
        patterns=(
            r"\bsincerely\b",
            r"\brespectfully submitted\b",
            r"\bregards\b",
            r"\bthank you for your (?:time and )?consideration,\s*\n",
        ),
    ),
    Falsifier(
        code="stamp_clock",
        basis="a:35210",
        why="uses citation-stamp times; the ruling binds spoken clock throughout",
        patterns=(r"\b(?:1[3-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?\b",),
    ),
    Falsifier(
        code="em_dash",
        basis="runbook:induction-authoring",
        why="contains an em- or en-dash",
        patterns=(r"[\u2014\u2013]",),
    ),
)


@dataclass
class Verdict:
    specimen: str
    citation: str
    chars: int
    breaches: list[tuple[Falsifier, list[str]]] = field(default_factory=list)

    @property
    def filable(self) -> bool:
        return not self.breaches and self.chars <= CHAR_CEILING

    @property
    def disqualifying(self) -> bool:
        """True when a breach goes to audience bind rather than to style."""
        return any(f.code == "audience_bind" for f, _ in self.breaches)


def assess(specimen: str, citation: str, text: str) -> Verdict:
    verdict = Verdict(specimen=specimen, citation=citation, chars=len(text))
    for falsifier in FALSIFIERS:
        hits = falsifier.hits(text)
        if hits:
            verdict.breaches.append((falsifier, hits))
    return verdict


def assess_arm(arm: str) -> list[Verdict]:
    verdicts = []
    for path in sorted(LETTERS.glob(f"{arm}-*.txt")):
        citation = path.stem.split("-", 1)[1]
        verdicts.append(assess(arm, citation, path.read_text().strip()))
    return verdicts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "arms",
        nargs="*",
        help="arm labels to gate (e.g. F4 G2). Default: every harvested arm.",
    )
    args = parser.parse_args()

    if args.arms:
        arms = args.arms
    else:
        arms = sorted({p.stem.split("-")[0] for p in LETTERS.glob("*.txt")})

    clean: list[str] = []
    repairable: list[str] = []
    blocked: list[str] = []

    for arm in arms:
        verdicts = assess_arm(arm)
        if not verdicts:
            print(f"{arm}: no letters found")
            continue
        if all(v.filable for v in verdicts):
            clean.append(arm)
            print(f"{arm}: FILABLE ({', '.join(str(v.chars) for v in verdicts)} chars)")
            continue
        if any(v.disqualifying for v in verdicts):
            blocked.append(arm)
        else:
            repairable.append(arm)
        print(f"{arm}: NEEDS REPAIR")
        for verdict in verdicts:
            if verdict.chars > CHAR_CEILING:
                print(f"    {verdict.citation}: over ceiling {verdict.chars}>{CHAR_CEILING}")
            for falsifier, hits in verdict.breaches:
                print(f"    {verdict.citation}: {falsifier.code} [{falsifier.basis}] "
                      f"{hits} — {falsifier.why}")

    print(f"\nfilable as written {len(clean)} · style repair {len(repairable)} · "
          f"audience-bind breach {len(blocked)} · of {len(arms)} assessed")
    if clean:
        print(f"filable: {' '.join(clean)}")
    if blocked:
        print(f"audience-bind breach (repair before any filing): {' '.join(blocked)}")
    print("\nIntersect this with the blind ranking, highest-ranked filable specimen wins.")
    print("Ranking is authoritative on quality; this gate is authoritative on filability.")


if __name__ == "__main__":
    main()
