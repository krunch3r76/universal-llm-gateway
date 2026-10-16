"""Old and new citation patterns agree under bool(search) (friction:39420)."""

from __future__ import annotations

import re

from services.rag.chunk_filters import _CITATION_LINE_RE

# Pattern before dropping the redundant ``.*`` wrappers.
_OLD = re.compile(
    r".*,\s*(?:19|20)\d{2}\b.*|.*\bet\s+al\.?\s*[,\.].*",
    re.IGNORECASE,
)

# Real lines: ulg-story/2026-10.md (plain) and indexed design docs (citations).
_CORPUS = (
    "Attention: an operator seat (asked-by not recorded) dispatched cursor-sdk a task on thread 13691.",
    "cursor-sdk finished Conductor unify — Lane-B git integrity in 88.1s.",
    "Attention: cursor-sdk failed on a task for an operator seat — worker_dispatch: failed.",
    "cursor-sdk finished Lane-B git integrity: capture ancestry and hub-path checkout in 253s.",
    "Attention: cursor-sdk finished a task (purpose not stated) in 71.9s.",
    "cursor-sdk finished Conductor unify — CDP display seat swap for cursor in 376s.",
    "Steer-inject a hop-admitted conductor from a bus-readable id in 35.4s.",
    "This is a targeted refresh, not a full re-projection of the subsystem.",
    "The staged corpus for step 9 covers 18 modules.",
    "See missing_coverage below rather than treating their absence as a retraction.",
    "Reference surface: in-text citation markers (`(Smith et al., 2024)`, `[15]`).",
    "PaperTrail (Martin-Boyle et al., CHI '26) is the closest sibling architecture.",
    "Bitemporal property graphs (Rost et al. 2021) extend the property graph model.",
    'Kadavath et al., "Language Models (Mostly) Know What They Know" — calibration.',
    "Post-v3 (Boot Redesign, 2026-04-07)",
    "inventory_sha=a71d07ced6db, 2026-07-19",
    "Exhibit 2 — Supplemental Assessment Notice (Jan 16, 2026)",
    "Determination & Corrected BOE-19-P (Sept 12, 2025)",
    "Good: Leginfo verbatim (directly fetched by claude-web 2026-05-13T03:43Z).",
    "Smith, J., 1999. A paper title.",
    "Doe et al. Something without a comma or period after al",
    "et al, 2010 is a citation line",
    "No year and no et-al token here at all.",
    "https://example.com/paper, 2020 is cited",
    "Table 1. Results for the 2018 cohort.",
    "plain prose about gateways and loops",
    "Jones et al. 2004, follow-up study",
    "see also (Ada et al. 1998) for the bound",
    "",
    "   ",
)
_PATHOLOGICAL = ("a, " * 3500) + "x"


def test_citation_search_parity_on_corpus_and_pathological_line() -> None:
    lines = list(_CORPUS) + [_PATHOLOGICAL]
    assert len(lines) >= 30
    for line in lines:
        assert bool(_OLD.search(line)) == bool(_CITATION_LINE_RE.search(line)), line
