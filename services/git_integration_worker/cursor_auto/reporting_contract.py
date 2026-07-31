"""Standing REPORTING CONTRACT block injected into every nested SDK prompt."""

from __future__ import annotations

from services.git_integration_worker.cursor_auto.section2_fields import (
    section2_emit_line,
)

DISPATCH_REPORT_DISCIPLINE_SKILL = "dispatch-report-discipline"

_REPORTING_CONTRACT_TEMPLATE = """\
## REPORTING CONTRACT (mandatory)

Your closeout MUST include §2 fields inline. Fill every checklist item below.
Negative answers are first-class — "not found", "cannot access", "not achievable
from this seat" are complete correct responses; never trade them for a plausible
positive.

Checklist — name each explicitly in §2:
- **SCOPE DELTA** — what was and was not done relative to what was asked
- **ACCESS** — what you could and could not reach (stated separately from result)
- **COVERAGE** — for every retrieval: corpus, count, actual date/ID range
- **MODEL ACTUAL** — resolved model when it differs from requested (in artifact body)

Mechanical rules (1–4, 11):
1. SUFFICIENCY — do enough to answer what was asked; subset OK, subset-as-whole is not
2. NEGATIVE ANSWERS ARE FIRST-CLASS — see above
3. NO SILENT SUBSTITUTION — model/scope/tool/method changes belong in the returned artifact
4. SCOPE DELTA ON EVERY CLOSEOUT — see checklist; cannot report complete without it
11. READ-ONLY TASKS STAY READ-ONLY

Prompt-side rules (5–8) — fill in §2:
5. VERBATIM FOR EVIDENCE — never paraphrase evidence; paraphrase launders interpretation
6. STATE COVERAGE BOUNDS — see COVERAGE; negative without bounds is uninterpretable
7. SURFACE CONTRADICTING EVIDENCE — do not merely answer the literal question
8. DISTINGUISH ABSENT FROM NOT-RETRIEVED — ACCESS status separate from result status

Judgment rules 9–10 live in the **dispatch-report-discipline** skill only — not gated here.

{section2_emit_line}\
"""

REPORTING_CONTRACT_BLOCK = _REPORTING_CONTRACT_TEMPLATE.format(
    section2_emit_line=section2_emit_line()
)


def reporting_contract_lines() -> list[str]:
    """Return the REPORTING CONTRACT block as prompt lines."""
    return ["", *REPORTING_CONTRACT_BLOCK.splitlines()]


__all__ = [
    "DISPATCH_REPORT_DISCIPLINE_SKILL",
    "REPORTING_CONTRACT_BLOCK",
    "reporting_contract_lines",
]
