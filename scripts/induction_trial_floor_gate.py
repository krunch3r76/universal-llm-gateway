#!/usr/bin/env python
"""Trial 01 floor gate — extract each arm's two letters and score them mechanically.

Reads the cursor-sdk closeout sidecars named by the admit receipts, pulls the two
citation letters out of the sidecar prose, and applies the pre-registered floor:
per-letter character ceiling, em-dash prohibition, and spoken-clock time format.
Also runs the R3 leakage scan for employer/workplace tokens.

Independence matters here: every count is recomputed from the extracted text
rather than read from the arm's own SELF-CHECK block, because a self-report is
the thing under test. Extraction failures are reported, never silently skipped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RECEIPTS = REPO / "tmp/induction-trial-01-admits.jsonl"
SIDECARS = REPO / "tmp/reviews/closeouts"
OUT_DIR = REPO / "tmp/induction-trial-01-letters"

CHAR_CEILING = 949
CITATIONS = ("14052781", "14052792")

# Arms whose admit receipt was never written (cell A k=1..3); recovered from the
# tip CHECKPOINT's thread block plus the closeout dispatch ids.
RECEIPT_GAP = {
    "11514": ("A", 1, False, "84d949c13ae2-82c07638"),
    "11515": ("A", 2, False, "c0f666d9d22c-6e8e0047"),
    "11516": ("A", 3, False, "d6b836e65bed-31fe0fa1"),
}

# R1-R4 retrieval factor: cells A-D inlined the facts, E-H fetched them via MCP.
RETRIEVED_CELLS = frozenset("EFGH")

EM_DASHES = ("\u2014", "\u2013")

# A stamp-style time is the failure mode; spoken clock is required.
STAMP_TIME = re.compile(r"\b(?:1[3-9]|2[0-3]):[0-5][0-9]")

# R3 leakage: employer / workplace identity was forbidden from every arm.
LEAK_TOKENS = (
    "walgreens",
    "pharmacy",
    "employer",
    "workplace",
    "my job",
    "my store",
    "the store",
    "my shift",
    "technician",
)

# Arms varied the header weight (``**CITATION x**`` vs a bare ``CITATION x`` line)
# and the trailing block labels, so both are matched loosely rather than literally.
HEADER = re.compile(r"^\**CITATION\s+(\d{8})\**\s*$", re.MULTILINE)

# The letter ends at whatever trailing block the arm chose: a labelled or bare
# character-count line, the route note, the self-check, or the manifest heading.
# Bare count lines matter — arms that used them had their counts and route prose
# absorbed into the letter, which inflated every measured length.
END_MARKER = re.compile(
    r"^(?:\**\d?\.?\s*Character counts"
    r"|\**Route\b"
    r"|\**SELF-CHECK"
    r"|\**Assertion ids"
    r"|\d{8}\s*[:=]"
    r"|\d{3,4}\s*$"
    r"|##\s)",
    re.MULTILINE,
)

# Each arm self-reports its own character counts; parsing them lets the gate
# compare claim against measurement instead of trusting either alone.
SELF_COUNT = re.compile(r"(\d{8})\D{0,40}?(\d{3,4})\b")

# Cells E-H fetched their facts from the graph; the ids they name are the audit
# trail for what retrieval actually delivered.
FETCHED_IDS = re.compile(r"\b3(?:1[0-9]{3}|4[0-9]{3})\b")


@dataclass
class LetterScore:
    citation: str
    chars: int
    em_dash: bool
    stamp_times: list[str] = field(default_factory=list)
    leaks: list[str] = field(default_factory=list)
    claimed_chars: int | None = None

    @property
    def self_report_drift(self) -> int | None:
        """Signed difference between the arm's claimed count and the measured one."""
        if self.claimed_chars is None:
            return None
        return self.claimed_chars - self.chars

    @property
    def over_ceiling(self) -> bool:
        return self.chars > CHAR_CEILING

    @property
    def passed(self) -> bool:
        return not (self.over_ceiling or self.em_dash or self.stamp_times)


@dataclass
class ArmResult:
    thread: str
    cell: str
    k: int
    retrieved: bool
    dispatch_id: str
    letters: list[LetterScore] = field(default_factory=list)
    fetched_ids: list[str] = field(default_factory=list)
    extract_error: str | None = None

    @property
    def passed(self) -> bool:
        return (
            self.extract_error is None
            and len(self.letters) == len(CITATIONS)
            and all(letter.passed for letter in self.letters)
        )


def load_arms() -> list[tuple[str, str, int, bool, str]]:
    """Return ``(thread, cell, k, retrieved, dispatch_id)`` for all 40 arms."""
    arms: dict[str, tuple[str, int, bool, str]] = dict(RECEIPT_GAP)
    for line in RECEIPTS.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        arms[row["thread"]] = (row["cell"], row["k"], row["mcp"], row["dispatch_id"])
    return [
        (thread, cell, k, mcp, dispatch_id)
        for thread, (cell, k, mcp, dispatch_id) in sorted(arms.items())
    ]


def _slice_letters(body: str) -> dict[str, str]:
    """Return ``{citation: letter_text}`` for every CITATION header in the sidecar.

    Each letter runs from its header to whichever comes first: the next header or
    the arm's trailing self-check / manifest block.
    """
    headers = list(HEADER.finditer(body))
    letters: dict[str, str] = {}
    for index, match in enumerate(headers):
        citation = match.group(1)
        start = match.end()
        stop = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        marker = END_MARKER.search(body, start, stop)
        if marker:
            stop = marker.start()
        text = body[start:stop].strip()
        # A repeated header (self-check echo) must not clobber the real letter.
        if text and citation not in letters:
            letters[citation] = text
    return letters


def _self_reported(body: str) -> dict[str, int]:
    """Parse the arm's own claimed character counts, when it stated them."""
    match = re.search(r"Character counts.{0,200}", body, re.DOTALL)
    if not match:
        return {}
    return {
        citation: int(count)
        for citation, count in SELF_COUNT.findall(match.group(0))
        if citation in CITATIONS
    }


def score_letter(citation: str, text: str, claimed: int | None) -> LetterScore:
    lowered = text.lower()
    return LetterScore(
        citation=citation,
        chars=len(text),
        em_dash=any(dash in text for dash in EM_DASHES),
        stamp_times=STAMP_TIME.findall(text),
        leaks=[token for token in LEAK_TOKENS if token in lowered],
        claimed_chars=claimed,
    )


def score_arm(thread: str, cell: str, k: int, mcp: bool, dispatch_id: str) -> ArmResult:
    result = ArmResult(
        thread=thread,
        cell=cell,
        k=k,
        retrieved=cell in RETRIEVED_CELLS,
        dispatch_id=dispatch_id,
    )
    sidecar = SIDECARS / f"{dispatch_id}.md"
    if not sidecar.is_file():
        result.extract_error = "sidecar_missing"
        return result
    body = sidecar.read_text(errors="replace")
    letters = _slice_letters(body)
    claimed = _self_reported(body)
    # Scope id-harvest to the arm's own prose, ahead of the wrapper manifest.
    prose = body[: body.find("## effects_manifest")] if "## effects_manifest" in body else body
    result.fetched_ids = sorted(set(FETCHED_IDS.findall(prose)))
    for citation in CITATIONS:
        text = letters.get(citation)
        if not text:
            result.extract_error = f"no_letter_{citation}"
            return result
        result.letters.append(score_letter(citation, text, claimed.get(citation)))
        (OUT_DIR / f"{cell}{k}-{citation}.txt").write_text(text + "\n")
    return result


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = [score_arm(*arm) for arm in load_arms()]

    print(f"{'arm':5}{'thr':7}{'retr':6}{'L1':7}{'L2':7}{'gate':6}{'ids':4} notes")
    for r in results:
        if r.extract_error:
            print(f"{r.cell}{r.k:<4}{r.thread:7}{'yes' if r.retrieved else 'no':6}"
                  f"{'-':7}{'-':7}{'ERR':6} {r.extract_error}")
            continue
        notes = []
        for letter in r.letters:
            if letter.over_ceiling:
                notes.append(f"{letter.citation} over ceiling ({letter.chars})")
            if letter.em_dash:
                notes.append(f"{letter.citation} em-dash")
            if letter.stamp_times:
                notes.append(f"{letter.citation} stamp-time {letter.stamp_times}")
            if letter.leaks:
                notes.append(f"{letter.citation} LEAK {letter.leaks}")
            drift = letter.self_report_drift
            if drift is not None and abs(drift) > 25:
                notes.append(f"{letter.citation} self-report off by {drift:+d}")
        print(f"{r.cell}{r.k:<4}{r.thread:7}{'yes' if r.retrieved else 'no':6}"
              f"{r.letters[0].chars:<7}{r.letters[1].chars:<7}"
              f"{'PASS' if r.passed else 'FAIL':6}{len(r.fetched_ids):<4} "
              f"{'; '.join(notes)}")

    scored = [r for r in results if not r.extract_error]
    passed = [r for r in scored if r.passed]
    leaked = [r for r in scored if any(letter.leaks for letter in r.letters)]
    print(f"\narms {len(results)} · extracted {len(scored)} · "
          f"floor PASS {len(passed)} · leakage {len(leaked)}")

    for label, subset in (("retrieved (E-H)", True), ("inlined (A-D)", False)):
        group = [r for r in scored if r.retrieved is subset]
        if not group:
            continue
        ok = sum(1 for r in group if r.passed)
        avg = sum(letter.chars for r in group for letter in r.letters) / (2 * len(group))
        print(f"  {label:16} n={len(group):<3} floor PASS {ok:<3} mean chars/letter {avg:.0f}")

    print(f"\nletters written to {OUT_DIR.relative_to(REPO)}/")


if __name__ == "__main__":
    main()
