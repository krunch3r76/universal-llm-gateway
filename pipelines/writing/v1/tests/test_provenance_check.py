"""Provenance-check tests for writer-specialist v1.

The pure checker is the subject. No client, network, or model is constructed.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

_PKG = "writing_v1_handlers"
if _PKG not in sys.modules:
    _HANDLERS = Path(__file__).resolve().parents[1] / "handlers"
    _spec = importlib.util.spec_from_file_location(
        _PKG,
        _HANDLERS / "__init__.py",
        submodule_search_locations=[str(_HANDLERS)],
    )
    assert _spec is not None and _spec.loader is not None
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[_PKG] = _mod
    _spec.loader.exec_module(_mod)

provenance = importlib.import_module("writing_v1_handlers.provenance_check")
email_envelope = importlib.import_module("writing_v1_handlers.email_envelope")

pytestmark = pytest.mark.offline

_LEDGER = [
    {"pin_id": "P1", "attested": True, "excerpt": "backed"},
    {"pin_id": "P2", "attested": False, "excerpt": "rumor"},
]


def _draft(text: str, claims: list[dict]) -> dict:
    return {"draft": text, "claims": claims, "need": [], "dispositions": []}


def _types(violations: list[dict]) -> list[str]:
    return [row["type"] for row in violations]


def test_provenance_unknown_pin() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Said it.",
            [
                {
                    "claim_id": "c1",
                    "text": "Said it.",
                    "pin_ids": ["P9"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "unknown_pin" in _types(violations)
    row = next(item for item in violations if item["type"] == "unknown_pin")
    assert row["claim_id"] == "c1"
    assert row["detail"] == "P9"


def test_provenance_unattested_fact() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "The rumor is fact.",
            [
                {
                    "claim_id": "c2",
                    "text": "The rumor is fact.",
                    "pin_ids": ["P2"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "unattested_pin" in _types(violations)
    assert "unknown_pin" not in _types(violations)


def test_provenance_pinless_claim_needs_marker() -> None:
    marked = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Color is [NEED: wavelength].",
            [
                {
                    "claim_id": "c3",
                    "text": "Color is [NEED: wavelength].",
                    "pin_ids": [],
                    "disposition": "express",
                    "state": "need",
                }
            ],
        ),
    )
    unverified = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Maybe [unverified: claim].",
            [
                {
                    "claim_id": "c4",
                    "text": "Maybe [unverified: claim].",
                    "pin_ids": [],
                    "disposition": "imply",
                    "state": "unverified",
                }
            ],
        ),
    )
    unmarked = provenance.check_provenance(
        _LEDGER,
        _draft(
            "The sky is green.",
            [
                {
                    "claim_id": "c5",
                    "text": "The sky is green.",
                    "pin_ids": [],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "pinless_claim_unmarked" not in _types(marked)
    assert "pinless_claim_unmarked" not in _types(unverified)
    assert "pinless_claim_unmarked" in _types(unmarked)


def test_provenance_em_dash() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Left \u2014 right \u2014 end.",
            [
                {
                    "claim_id": "c1",
                    "text": "Left.",
                    "pin_ids": ["P1"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    rows = [row for row in violations if row["type"] == "em_dash"]
    assert len(rows) == 1
    assert rows[0]["claim_id"] is None
    assert rows[0]["detail"] == 2


def test_provenance_persona_line() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "I am an AI helper.\nBacked.",
            [
                {
                    "claim_id": "c1",
                    "text": "Backed.",
                    "pin_ids": ["P1"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "persona_line" in _types(violations)


def test_provenance_bolted_closer() -> None:
    violations = provenance.check_provenance(
        _LEDGER,
        _draft(
            "Backed sentence.\n\nIn summary, that is all.",
            [
                {
                    "claim_id": "c1",
                    "text": "Backed sentence.",
                    "pin_ids": ["P1"],
                    "disposition": "express",
                    "state": "backed",
                }
            ],
        ),
    )
    assert "bolted_closer" in _types(violations)


def test_provenance_draft_unparseable() -> None:
    for bad in (None, {"claims": []}, ["nope"]):
        violations = provenance.check_provenance(_LEDGER, bad)
        assert _types(violations) == ["draft_unparseable"]
        assert violations[0]["claim_id"] is None


_ASK_LEDGER = [
    {"pin_id": "P5", "attested": False},
    {"pin_id": "P6", "attested": False},
    {"pin_id": "P7", "attested": False},
    {"pin_id": "P1", "attested": True},
]
_C2 = (
    "could you send me the specific complaints in writing: which stores, "
    "which dates, and what was reported?"
)
_C3 = "Could you also confirm the date of my review?"


def test_provenance_request_asks_are_not_unattested() -> None:
    draft = _draft(
        f"{_C2} {_C3}",
        [
            {
                "claim_id": "c2",
                "text": _C2,
                "pin_ids": ["P5", "P6"],
                "disposition": "request",
                "state": "need",
            },
            {
                "claim_id": "c3",
                "text": _C3,
                "pin_ids": ["P7"],
                "disposition": "request",
                "state": "need",
            },
        ],
    )
    assert provenance.check_provenance(_ASK_LEDGER, draft) == []
    express = _draft(
        f"{_C2} {_C3}",
        [
            {
                "claim_id": "c2",
                "text": _C2,
                "pin_ids": ["P5", "P6"],
                "disposition": "express",
                "state": "backed",
            },
            {
                "claim_id": "c3",
                "text": _C3,
                "pin_ids": ["P7"],
                "disposition": "express",
                "state": "backed",
            },
        ],
    )
    kinds = _types(provenance.check_provenance(_ASK_LEDGER, express))
    assert kinds.count("unattested_pin") == 3


def test_provenance_request_cannot_cite_attested_pin() -> None:
    """A request that cites attested=true is refused, not exempt."""
    violations = provenance.check_provenance(
        _ASK_LEDGER,
        _draft(
            "Could you confirm the date?",
            [
                {
                    "claim_id": "c1",
                    "text": "Could you confirm the date?",
                    "pin_ids": ["P1"],
                    "disposition": "request",
                    "state": "need",
                }
            ],
        ),
    )
    assert "request_cites_attested" in _types(violations)
    assert "unattested_pin" not in _types(violations)


def test_provenance_revision_catches_new_clause_and_dropped_name() -> None:
    v1 = _draft(
        "I spoke with Jennifer about the record.",
        [
            {
                "claim_id": "c1",
                "text": "I spoke with Jennifer about the record.",
                "pin_ids": ["P1"],
                "disposition": "express",
                "state": "backed",
            }
        ],
    )
    v2 = _draft(
        "I spoke about the record. as I was told on the call.",
        [
            {
                "claim_id": "c1",
                "text": "I spoke about the record.",
                "pin_ids": ["P1"],
                "disposition": "express",
                "state": "backed",
            }
        ],
    )
    kinds = _types(provenance.revision_delta(v1, v2))
    assert "v2_new_unpinned_clause" in kinds
    assert "v2_dropped_name" in kinds
    dropped = [
        row
        for row in provenance.revision_delta(v1, v2)
        if row["type"] == "v2_dropped_name"
    ]
    assert dropped[0]["detail"] == "Jennifer"


def test_revision_delta_contraction_expansion_no_false_drops() -> None:
    draft_v1 = (
        "Hi Maria, I can't cover the PHC Petaluma shifts on 11/3. Thanks."
    )
    draft_v2 = (
        "Hi Maria, I cannot cover the PHC Petaluma shifts on 11/3. Thanks."
    )
    v1 = {"draft": draft_v1}
    v2 = {"draft": draft_v2}
    assert provenance.revision_delta(v1, v2) == []


def test_causal_connective_clause_joining_so() -> None:
    pattern = provenance._CAUSAL_CONNECTIVES_RE
    merge = (
        "Fill was 251 when I opened Saturday and 213 Sunday morning, "
        "so two days of work and it's sitting where it started."
    )
    assert pattern.search(merge).group(0).strip().lower() == "so"
    assert pattern.search("So two days of work.").group(0).strip().lower() == "so"
    assert pattern.search("I was so tired on Saturday.") is None
    assert pattern.search("because").group(0).lower() == "because"
    assert pattern.search(", so that we").group(0).lower() == "so that"


def test_prose_rhythm_subordinate_clause_merge_preserves_pin_trace() -> None:
    v1 = (
        "Fill was 251 when I opened Saturday. "
        "It was 213 Sunday morning. "
        "So two days of work and it's sitting where it started."
    )
    v2 = (
        "Fill was 251 when I opened Saturday and 213 Sunday morning, "
        "so two days of work and it's sitting where it started."
    )
    v1_clauses = [
        {"text": "Fill was 251 when I opened Saturday.", "pin_ids": ["P1"]},
        {"text": "It was 213 Sunday morning.", "pin_ids": ["P2"]},
        {
            "text": "So two days of work and it's sitting where it started.",
            "pin_ids": ["P3"],
        },
    ]
    v2_clauses = [
        {
            "text": v2,
            "pin_ids": ["P1", "P2", "P3"],
        }
    ]
    assert (
        provenance.revision_delta(
            {"draft": v1, "claims": v1_clauses},
            {"draft": v2, "claims": v2_clauses},
            v1_clauses=v1_clauses,
            v2_clauses=v2_clauses,
        )
        == []
    )


@pytest.mark.parametrize(
    ("v2", "kind"),
    [
        (
            "Fill was 251 when I opened Saturday and 213 Sunday morning, because extra.",
            "fact_added",
        ),
        (
            "It was 213 Sunday morning.",
            "v2_dropped_number",
        ),
        (
            "Fill seems to be 251 when I opened Saturday. It was 213 Sunday morning. So two days.",
            "fact_hedged",
        ),
        (
            "It was 213 Sunday morning. Fill was 251 when I opened Saturday. So two days.",
            "fact_reordered",
        ),
    ],
)
def test_fact_ceiling_final_detects_mutations(v2: str, kind: str) -> None:
    v1 = (
        "Fill was 251 when I opened Saturday. "
        "It was 213 Sunday morning. "
        "So two days of work and it's sitting where it started."
    )
    v1_clauses = [
        {"text": "Fill was 251 when I opened Saturday.", "pin_ids": ["P1"]},
        {"text": "It was 213 Sunday morning.", "pin_ids": ["P2"]},
        {
            "text": "So two days of work and it's sitting where it started.",
            "pin_ids": ["P3"],
        },
    ]
    v2_clauses = list(v1_clauses)
    if kind == "fact_reordered":
        v2_clauses = [v1_clauses[1], v1_clauses[0], v1_clauses[2]]
    kinds = _types(
        provenance.revision_delta(
            {"draft": v1, "claims": v1_clauses},
            {"draft": v2, "claims": v2_clauses},
            v1_clauses=v1_clauses,
            v2_clauses=v2_clauses,
        )
    )
    assert kind in kinds


class _Out:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.json = payload


class _Ctx:
    def __init__(self, outputs: dict[str, Any]) -> None:
        self.outputs = outputs


class _Step:
    def __init__(self) -> None:
        self.handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "draft.json",
        }


def test_provenance_check_unknown_pin_from_fact_ceiling_audit_clauses() -> None:
    draft = _draft(
        "Backed.",
        [
            {
                "claim_id": "c1",
                "text": "Backed.",
                "pin_ids": ["P1"],
                "disposition": "express",
                "state": "backed",
            }
        ],
    )
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _Step(),
            _Ctx(
                {
                    "assemble": _Out({"pin_ledger": _LEDGER}),
                    "draft": _Out(draft),
                    "fact_ceiling_audit": _Out(
                        {
                            "findings": [],
                            "verdict": "ship",
                            "clauses": [
                                {
                                    "text": "Unknown cite.",
                                    "pin_ids": ["P9"],
                                    "disposition": "express",
                                }
                            ],
                        }
                    ),
                }
            ),
        )
    ).json
    assert payload is not None
    kinds = _types(payload["violations"])
    assert "unknown_pin" in kinds
    row = next(item for item in payload["violations"] if item["type"] == "unknown_pin")
    assert row["detail"] == "P9"


_FILL_LEDGER = [
    {
        "pin_id": "P1",
        "attested": True,
        "excerpt": "Fill was 251 when I opened Saturday",
    },
    {"pin_id": "P2", "attested": True, "excerpt": "It was 213 Sunday morning"},
    {
        "pin_id": "P3",
        "attested": True,
        "excerpt": "So two days of work and it's sitting where it started",
    },
]
_FILL_V1 = (
    "Fill was 251 when I opened Saturday. "
    "It was 213 Sunday morning. "
    "So two days of work and it's sitting where it started."
)
_FILL_V1_CLAUSES = [
    {
        "text": "Fill was 251 when I opened Saturday.",
        "pin_ids": ["P1"],
        "disposition": "express",
    },
    {"text": "It was 213 Sunday morning.", "pin_ids": ["P2"], "disposition": "express"},
    {
        "text": "So two days of work and it's sitting where it started.",
        "pin_ids": ["P3"],
        "disposition": "express",
    },
]


def test_fixture_saturday_drop_is_drop_finding() -> None:
    v2 = (
        "Fill was 251 when I opened. "
        "It was 213 Sunday morning. "
        "So two days of work and it's sitting where it started."
    )
    v2_clauses = provenance.derive_v2_clauses_from_audit(
        v2, _FILL_V1_CLAUSES, _FILL_LEDGER
    )
    violations = provenance.revision_delta(
        {"draft": _FILL_V1, "claims": _FILL_V1_CLAUSES},
        {"draft": v2, "claims": v2_clauses},
        v1_clauses=_FILL_V1_CLAUSES,
        v2_clauses=v2_clauses,
        pin_ledger=_FILL_LEDGER,
    )
    kinds = _types(violations)
    assert "fact_dropped" in kinds
    assert any(
        row["type"] == "fact_dropped" and row["detail"] == "Saturday"
        for row in violations
    )


class _Raw:
    def __init__(self, text: str) -> None:
        self.raw = text


class _StepFactCeilingFinal:
    def __init__(self) -> None:
        self.handler_inputs = {
            "pin_ledger": "assemble.json.pin_ledger",
            "draft": "prose_rhythm_pass.raw",
            "draft_v1": "draft.json",
        }


def _run_fact_ceiling_final(
    *,
    v2_raw: str,
    v1_draft: str,
    v1_clauses: list[dict[str, Any]],
    pin_ledger: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _StepFactCeilingFinal(),
            _Ctx(
                {
                    "assemble": _Out({"pin_ledger": pin_ledger}),
                    "draft": _Out(
                        {
                            "ok": True,
                            "draft": v1_draft,
                            "selected_role": "drafter_openai",
                        }
                    ),
                    "fact_ceiling_audit": _Out(
                        {
                            "findings": [],
                            "verdict": "ship",
                            "clauses": v1_clauses,
                        }
                    ),
                    "prose_rhythm_pass": _Raw(v2_raw),
                }
            ),
        )
    ).json
    assert payload is not None
    return payload["violations"]


def test_fact_ceiling_final_probe_a_fact_added() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw=(
            "Fill was 251 when I opened Saturday and 213 Sunday morning because the pump failed, "
            "so two days of work and it's sitting where it started."
        ),
        v1_draft=_FILL_V1,
        v1_clauses=_FILL_V1_CLAUSES,
        pin_ledger=_FILL_LEDGER,
    )
    added = [row for row in violations if row["type"] == "fact_added"]
    assert added
    assert added[0]["detail"] == (
        "Fill was 251 when I opened Saturday and 213 Sunday morning because the pump failed, "
        "so two days of work and it's sitting where it started."
    )


def test_fact_ceiling_final_probe_b_fact_added() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw=(
            "Fill was 251 when I opened Saturday because the pump failed. "
            "It was 213 Sunday morning. "
            "So two days of work and it's sitting where it started."
        ),
        v1_draft=_FILL_V1,
        v1_clauses=_FILL_V1_CLAUSES,
        pin_ledger=_FILL_LEDGER,
    )
    added = [row for row in violations if row["type"] == "fact_added"]
    assert added
    assert added[0]["detail"] == (
        "Fill was 251 when I opened Saturday because the pump failed."
    )


def test_fact_ceiling_final_merged_p2_before_p1_fact_reordered() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw=(
            "Fill was 213 Sunday morning, and fill was 251 when I opened Saturday, "
            "so two days of work and it's sitting where it started."
        ),
        v1_draft=_FILL_V1,
        v1_clauses=_FILL_V1_CLAUSES,
        pin_ledger=_FILL_LEDGER,
    )
    assert "fact_reordered" in _types(violations)


_RENT_V1 = "Rent is 1200. Rent goes up to 1350 in March."
_RENT_V1_CLAUSES = [
    {"text": "Rent is 1200.", "pin_ids": ["P1"], "disposition": "express"},
    {
        "text": "Rent goes up to 1350 in March.",
        "pin_ids": ["P2"],
        "disposition": "express",
    },
]
_RENT_LEDGER = [
    {"pin_id": "P1", "attested": True, "excerpt": "Rent is 1200"},
    {"pin_id": "P2", "attested": True, "excerpt": "Rent goes up to 1350 in March"},
]


def test_fact_ceiling_final_rent_merge_p2_before_p1_fact_reordered() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw="Rent goes up to 1350 in March and rent is 1200.",
        v1_draft=_RENT_V1,
        v1_clauses=_RENT_V1_CLAUSES,
        pin_ledger=_RENT_LEDGER,
    )
    assert "fact_reordered" in _types(violations)


def test_fact_ceiling_final_section_91_merge_still_passes() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw=(
            "Fill was 251 when I opened Saturday and 213 Sunday morning, "
            "so two days of work and it's sitting where it started."
        ),
        v1_draft=_FILL_V1,
        v1_clauses=_FILL_V1_CLAUSES,
        pin_ledger=_FILL_LEDGER,
    )
    assert violations == []


_PARAPHRASE_V1 = (
    "On Saturday morning the fill level read 251. "
    "By Sunday it had dropped to 213. "
    "Two days of work, and we are back where we started."
)
_PARAPHRASE_CLAUSES = [
    {
        "text": "On Saturday morning the fill level read 251.",
        "pin_ids": ["P1"],
        "disposition": "express",
    },
    {
        "text": "By Sunday it had dropped to 213.",
        "pin_ids": ["P2"],
        "disposition": "express",
    },
    {
        "text": "Two days of work, and we are back where we started.",
        "pin_ids": ["P3"],
        "disposition": "express",
    },
]
_PARAPHRASE_LEDGER = [
    {
        "pin_id": "P1",
        "attested": True,
        "excerpt": "Fill was 251 when I opened Saturday",
    },
    {"pin_id": "P2", "attested": True, "excerpt": "It was 213 Sunday morning"},
    {
        "pin_id": "P3",
        "attested": True,
        "excerpt": "So two days of work and it's sitting where it started",
    },
]


def test_fact_ceiling_final_paraphrase_swap_fact_reordered() -> None:
    v2 = (
        "By Sunday it had dropped to 213. "
        "On Saturday morning the fill level read 251. "
        "Two days of work, and we are back where we started."
    )
    violations = _run_fact_ceiling_final(
        v2_raw=v2,
        v1_draft=_PARAPHRASE_V1,
        v1_clauses=_PARAPHRASE_CLAUSES,
        pin_ledger=_PARAPHRASE_LEDGER,
    )
    assert "fact_reordered" in _types(violations)


def test_fact_ceiling_final_r2_probe_merged_fact_reordered() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw=(
            "It was 213 Sunday morning, and fill was 251 when I opened Saturday, "
            "so two days of work and it's sitting where it started."
        ),
        v1_draft=_FILL_V1,
        v1_clauses=_FILL_V1_CLAUSES,
        pin_ledger=_FILL_LEDGER,
    )
    assert "fact_reordered" in _types(violations)


def test_provenance_check_rejects_prior_draft_only_and_intake_answer_pins() -> None:
    ledger = [
        {
            "pin_id": "P1",
            "attested": False,
            "provenance": "prior_draft_only",
            "excerpt": "The garage door was painted blue during 2019.",
        },
        {
            "pin_id": "P2",
            "attested": False,
            "provenance": "intake_answer",
            "excerpt": "I do not have phone records of these calls.",
        },
    ]
    draft = _draft(
        "The garage door was painted blue during 2019. I do not have phone records of these calls.",
        [
            {
                "claim_id": "c1",
                "text": "The garage door was painted blue during 2019.",
                "pin_ids": ["P1"],
                "disposition": "express",
            },
            {
                "claim_id": "c2",
                "text": "I do not have phone records of these calls.",
                "pin_ids": ["P2"],
                "disposition": "express",
            },
        ],
    )
    details = [
        str(row["detail"])
        for row in provenance.check_provenance(ledger, draft)
        if row["type"] == "non_attested_provenance"
    ]
    assert "P1:prior_draft_only" in details
    assert "P2:intake_answer" in details


def test_fact_ceiling_final_blocks_ship_gate_on_drift_check_concession() -> None:
    concession = "I received the January 16, 2026 notice at 15571 Flintridge Dr close to its date."
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _StepFactCeilingFinal(),
            _Ctx(
                {
                    "assemble": _Out({"pin_ledger": _FILL_LEDGER, "channel": "filing"}),
                    "draft": _Out({"draft": _FILL_V1, "claims": _FILL_V1_CLAUSES}),
                    "fact_ceiling_audit": _Out(
                        {"findings": [], "verdict": "ship", "clauses": _FILL_V1_CLAUSES}
                    ),
                    "prose_rhythm_pass": _Raw(concession),
                }
            ),
        )
    ).json
    assert payload is not None
    assert payload["pass"] is False
    assert "purpose_drift" in _types(payload["violations"])
    assert payload["drift_check"]["passed"] is False
    assert any(
        row.get("reason") == "volunteered_concession"
        for row in payload["drift_check"]["flagged_sentences"]
    )


def test_fact_ceiling_final_derived_unpinned_new_sentence() -> None:
    violations = _run_fact_ceiling_final(
        v2_raw=(
            "Fill was 251 when I opened Saturday and 213 Sunday morning. "
            "It was 251 when I opened Sunday. "
            "So two days of work and it's sitting where it started."
        ),
        v1_draft=_FILL_V1,
        v1_clauses=_FILL_V1_CLAUSES,
        pin_ledger=_FILL_LEDGER,
    )
    assert violations
    assert "v2_new_unpinned_clause" in _types(violations)


def test_revision_delta_email_channel_skips_fact_reordered() -> None:
    v1_text = "Alpha fact one. Beta fact two."
    v1_clauses = [
        {
            "claim_id": "s1",
            "text": "Alpha fact one.",
            "pin_ids": ["P2", "P6"],
            "disposition": "express",
        },
        {
            "claim_id": "s2",
            "text": "Beta fact two.",
            "pin_ids": ["P3"],
            "disposition": "express",
        },
    ]
    v2_clauses = [
        {
            "claim_id": "s1",
            "text": "Alpha fact one.",
            "pin_ids": ["P3"],
            "disposition": "express",
        },
        {
            "claim_id": "s2",
            "text": "Beta fact two.",
            "pin_ids": ["P2", "P6"],
            "disposition": "express",
        },
    ]
    email_violations = provenance.revision_delta(
        {"draft": v1_text},
        {"draft": v1_text},
        v1_clauses=v1_clauses,
        v2_clauses=v2_clauses,
        channel="email",
    )
    assert "fact_reordered" not in _types(email_violations)
    filing_violations = provenance.revision_delta(
        {"draft": v1_text},
        {"draft": v1_text},
        v1_clauses=v1_clauses,
        v2_clauses=v2_clauses,
        channel="filing",
    )
    assert "fact_reordered" in _types(filing_violations)


def test_revision_delta_enumeration_vs_range_flagged() -> None:
    and_to_through = provenance.revision_delta(
        {"draft": "Attached are Exhibits 2 and 8."},
        {"draft": "Attached are Exhibits 2 through 8."},
        channel="filing",
    )
    assert "fact_added" in _types(and_to_through)
    and_to_hyphen = provenance.revision_delta(
        {"draft": "Attached are Exhibits 2 and 8."},
        {"draft": "Attached are Exhibits 2-8."},
        channel="filing",
    )
    assert "fact_added" in _types(and_to_hyphen)
    through_to_and = provenance.revision_delta(
        {"draft": "Attached are Exhibits 2 through 8."},
        {"draft": "Attached are Exhibits 2 and 8."},
        channel="filing",
    )
    assert "fact_dropped" in _types(through_to_and)
    dropped = [v for v in through_to_and if v.get("type") == "fact_dropped"]
    assert any(v.get("detail") == "through" for v in dropped)


def test_revision_delta_sworn_in_vs_through_non_numeric_flagged() -> None:
    in_to_through = provenance.revision_delta(
        {"draft": "I resided there in March 2026."},
        {"draft": "I resided there through March 2026."},
        channel="filing",
    )
    assert "fact_added" in _types(in_to_through)
    through_to_in = provenance.revision_delta(
        {"draft": "I resided there through March 2026."},
        {"draft": "I resided there in March 2026."},
        channel="filing",
    )
    dropped = [v for v in through_to_in if v.get("type") == "fact_dropped"]
    assert dropped
    assert any(v.get("detail") == "through" for v in dropped)


def test_derive_v2_clauses_from_audit_skips_uncited_non_restate_pin_when_audit_pins_present() -> (
    None
):
    v1_clauses = [
        {"text": "Alpha fact.", "pin_ids": ["P1"], "disposition": "express"},
        {"text": "Beta fact.", "pin_ids": ["P2"], "disposition": "express"},
    ]
    ledger = [
        {"pin_id": "P1", "attested": True, "excerpt": "Alpha fact."},
        {"pin_id": "P2", "attested": True, "excerpt": "Beta fact."},
        {"pin_id": "P3", "attested": True, "excerpt": "Only on ledger."},
    ]
    v2_rows = provenance.derive_v2_clauses_from_audit(
        "Alpha fact. Beta fact.", v1_clauses, ledger
    )
    assert all("P3" not in (row.get("pin_ids") or []) for row in v2_rows)
    violations = provenance.revision_delta(
        {"draft": "Alpha fact. Beta fact."},
        {"draft": "Alpha fact. Beta fact."},
        v1_clauses=v1_clauses,
        v2_clauses=v2_rows,
        pin_ledger=ledger,
        v2_clauses_from_derive=True,
        channel="filing",
    )
    assert "fact_reordered" not in _types(violations)


_EXHIBIT_INTAKE_NOTES = [
    "Exhibit 4: Supplemental Tax Bills 10059585 and 10059586",
    "Exhibit 5: Invalid Closed Letters dated 2026-06-05",
    "Exhibit 6: Requests for Reinstatement stamped 2026-07-23",
    "Exhibit 8: Letter to Assessor stamped 2026-04-15",
]
_LEG_C18_V1_TAIL = (
    "Exhibit 4: Supplemental Tax Bills 10059585 and 10059586\n"
    "Exhibit 5: Invalid Closed Letters dated 2026-06-05\n"
    "Exhibit 6: Requests for Reinstatement stamped 2026-07-23\n"
    "Exhibit 7: Clerk Email of 2026-05-28 Acknowledging Documents Submitted 2026-05-27\n"
    "Exhibit 8: Letter to Assessor stamped 2026-04-15\n"
    "Sincerely,\n"
    "Kaywan Joseph Mansubi"
)
_LEG_C18_V2_TAIL = (
    "Exhibit 4 contains Supplemental Tax Bills 10059585 and 10059586.\n"
    "Exhibit 5 includes Invalid Closed Letters dated 2026-06-05.\n"
    "Exhibit 6 shows Requests for Reinstatement stamped 2026-07-23.\n"
    "Exhibit 7: Clerk Email of 2026-05-28 Acknowledging Documents Submitted 2026-05-27\n"
    "Exhibit 8: Letter to Assessor stamped 2026-04-15.\n"
    "Sincerely,\n"
    "Kaywan Joseph Mansubi"
)


def test_fact_ceiling_final_email_unpunctuated_exhibit_before_sincerely_passes() -> None:
    body = (
        "I am submitting documents for the reinstatement hearing. "
        "Attached are my declaration and Exhibits 2 through 8.\n"
    )
    v1_email = (
        "To: AABHearing@cob.sccgov.org\n"
        "Subject: Assessment Appeals 25.7489 and 25.7490\n\n"
        f"{body}{_LEG_C18_V1_TAIL}"
    )
    v2_email = (
        "To: AABHearing@cob.sccgov.org\n"
        "Subject: Assessment Appeals 25.7489 and 25.7490\n\n"
        f"{body}{_LEG_C18_V2_TAIL}"
    )
    intake = {
        "recipient": "AABHearing@cob.sccgov.org",
        "signer": "Kaywan Joseph Mansubi",
        "verbatim_notes": _EXHIBIT_INTAKE_NOTES,
    }
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _StepFactCeilingFinal(),
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "pin_ledger": [
                                {
                                    "pin_id": "P2",
                                    "attested": True,
                                    "excerpt": "Attached are my declaration and Exhibits 2 through 8.",
                                }
                            ],
                            "channel": "email",
                            "recipient": "AABHearing@cob.sccgov.org",
                            "intake": intake,
                        }
                    ),
                    "draft": _Out({"ok": True, "draft": v1_email}),
                    "fact_ceiling_audit": _Out(
                        {
                            "findings": [],
                            "verdict": "ship",
                            "clauses": [
                                {
                                    "text": body.strip(),
                                    "pin_ids": ["P2"],
                                    "disposition": "express",
                                }
                            ],
                        }
                    ),
                    "prose_rhythm_pass": _Raw(v2_email),
                }
            ),
        )
    ).json
    assert payload is not None
    assert payload["violations"] == []
    assert payload["pass"] is True


_LEG_C_BODY_1 = (
    "I am submitting the attached CCP § 2015.5 Declaration of Kaywan Joseph Mansubi "
    "Regarding Timeliness (Exhibit 1) and Exhibits 2-8 for the reinstatement hearing "
    "on Assessment Appeals 25.7489 and 25.7490."
)
_LEG_C_BODY_2 = (
    "The property is APN 424-20-043 at 15571 Flintridge Dr in Los Gatos and the hearing "
    "is Tuesday October 27 2026 at 9:00 AM PT on Zoom."
)
_LEG_C_V1_EMAIL = (
    "To: AABHearing@cob.sccgov.org\n"
    "Subject: Assessment Appeals 25.7489 and 25.7490, APN 424-20-043, "
    "Reinstatement Hearing October 27 2026\n\n"
    f"{_LEG_C_BODY_1} {_LEG_C_BODY_2} The exhibits are as follows.\n\n"
    "Exhibit 1: Declaration of Kaywan Joseph Mansubi Regarding Timeliness (CCP § 2015.5)\n"
    "Exhibit 2: Notice of Supplemental Assessment dated 2026-01-16\n"
    "Sincerely\nKaywan Joseph Mansubi"
)
_LEG_C_V2_EMAIL = (
    "To: AABHearing@cob.sccgov.org\n"
    "Subject: Assessment Appeals 25.7489 and 25.7490, APN 424-20-043, "
    "Reinstatement Hearing October 27 2026\n\n"
    "I am submitting the attached CCP § 2015.5 Declaration of Kaywan Joseph Mansubi "
    "Regarding Timeliness (Exhibit 1) and Exhibits 2 through 8 for the reinstatement "
    "hearing on Assessment Appeals 25.7489 and 25.7490. The property is APN 424-20-043 "
    "at 15571 Flintridge Dr in Los Gatos, and the hearing is Tuesday October 27 2026 "
    "at 9:00 AM PT on Zoom. The exhibits are as follows.\n"
    "Exhibit 1: Declaration of Kaywan Joseph Mansubi Regarding Timeliness (CCP § 2015.5)\n"
    "Exhibit 2: Notice of Supplemental Assessment dated 2026-01-16\n"
    "Sincerely\nKaywan Joseph Mansubi"
)
_LEG_C_PIN_LEDGER = [
    {
        "pin_id": "P2",
        "attested": True,
        "restate": True,
        "excerpt": (
            "I, Kaywan Joseph Mansubi, am submitting my CCP § 2015.5 Declaration of "
            "Kaywan Joseph Mansubi Regarding Timeliness (Exhibit 1) plus Exhibits 2–8 "
            "for the reinstatement hearing on Assessment Appeals 25.7489 and 25.7490."
        ),
    },
    {"pin_id": "P3", "attested": True, "restate": True, "excerpt": "APN 424-20-043, 15571 Flintridge Dr, Los Gatos."},
    {
        "pin_id": "P4",
        "attested": True,
        "restate": True,
        "excerpt": "Hearing: Tuesday, October 27, 2026, 9:00 AM PT, on Zoom.",
    },
    {
        "pin_id": "P6",
        "attested": True,
        "restate": True,
        "excerpt": "Exhibit 1: Declaration of Kaywan Joseph Mansubi Regarding Timeliness (CCP § 2015.5)",
    },
]
_LEG_C_V1_CLAUSES = [
    {"claim_id": "s1", "text": _LEG_C_BODY_1, "pin_ids": ["P2"], "disposition": "express"},
    {"claim_id": "s2", "text": _LEG_C_BODY_2, "pin_ids": ["P3", "P4"], "disposition": "express"},
]


def test_revision_delta_s2_correspondence_closing_prose_not_exempt() -> None:
    v1 = {"draft": "Body only.\n\nSincerely,\nKaywan Joseph Mansubi"}
    v2 = {
        "draft": (
            "Body only.\n\nThank you\n"
            "The Assessor admitted in writing that the closure was an error."
        )
    }
    kinds = _types(provenance.revision_delta(v1, v2, channel="email"))
    assert "fact_added" in kinds
    assert "v2_new_unpinned_clause" in kinds


def test_revision_delta_s3_correspondence_closing_with_date_not_exempt() -> None:
    v1 = {"draft": "Body only.\n\nSincerely,\nKaywan Joseph Mansubi"}
    v2 = {
        "draft": (
            "Body only.\n\nSincerely,\n"
            "The clerk confirmed receipt on 2026-05-27."
        )
    }
    kinds = _types(provenance.revision_delta(v1, v2, channel="email"))
    assert "fact_added" in kinds


def test_revision_delta_s4_correspondence_modified_subject_flagged() -> None:
    v1 = {
        "draft": (
            "To: clerk@example.org\n"
            "Subject: Assessment Appeals 25.7489\n\n"
            "Body."
        )
    }
    v2 = {
        "draft": (
            "To: clerk@example.org\n"
            "Subject: Appeals 25.7489 wrongly closed after Assessor conceded error\n\n"
            "Body."
        )
    }
    kinds = _types(provenance.revision_delta(v1, v2, channel="email"))
    assert "fact_added" in kinds


def test_revision_delta_s5_filing_re_or_date_line_flagged() -> None:
    v1 = {"draft": "I declare under penalty of perjury.\n\n/s/ Signer"}
    v2_re = {
        "draft": (
            "I declare under penalty of perjury.\n\n"
            "Re: Notice never served on declarant\n\n/s/ Signer"
        )
    }
    kinds_re = _types(provenance.revision_delta(v1, v2_re, channel="filing"))
    assert "fact_added" in kinds_re
    v2_date = {
        "draft": (
            "I declare under penalty of perjury.\n\n"
            "Date: 2026-04-15\n\n/s/ Signer"
        )
    }
    kinds_date = _types(provenance.revision_delta(v1, v2_date, channel="filing"))
    assert "fact_added" in kinds_date


def test_normalize_exhibit_lines_n1_claim_line_not_scrubbed() -> None:
    intake = {"verbatim_notes": _EXHIBIT_INTAKE_NOTES}
    raw = (
        "Exhibit 8 shows Letter to Assessor stamped 2026-04-15 "
        "proving timely service."
    )
    out = email_envelope.polish_email_draft(
        raw,
        recipient="",
        signer="",
        intake=intake,
    )
    assert "proving timely service" in out
    v1 = {"draft": "Attached are exhibits.\nExhibit 8: Letter to Assessor stamped 2026-04-15"}
    v2 = {"draft": f"Attached are exhibits.\n{raw}"}
    kinds = _types(provenance.revision_delta(v1, v2, channel="email"))
    assert "fact_added" in kinds


def test_normalize_exhibit_lines_n2_multisentence_not_scrubbed() -> None:
    intake = {"verbatim_notes": _EXHIBIT_INTAKE_NOTES}
    raw = (
        "Exhibit 8 shows I wrote to the Assessor before the deadline. "
        "I then called the clerk on 2026-05-27."
    )
    out = email_envelope.polish_email_draft(
        raw,
        recipient="",
        signer="",
        intake=intake,
    )
    assert "I then called the clerk" in out
    v1 = {"draft": "Attached are exhibits.\nExhibit 8: Letter to Assessor stamped 2026-04-15"}
    v2 = {"draft": f"Attached are exhibits.\n{raw}"}
    kinds = _types(provenance.revision_delta(v1, v2, channel="email"))
    assert "fact_added" in kinds


def test_normalize_exhibit_lines_positive_exhibit_six_still_normalized() -> None:
    intake = {"verbatim_notes": _EXHIBIT_INTAKE_NOTES}
    raw = "Exhibit 6 shows the Requests for Reinstatement stamped 2026-07-23."
    out = email_envelope.polish_email_draft(
        raw,
        recipient="",
        signer="",
        intake=intake,
    )
    assert "Exhibit 6: Requests for Reinstatement stamped 2026-07-23" in out


def test_fact_ceiling_final_email_range_through_and_restate_pin_passes() -> None:
    _gates_path = Path(__file__).resolve().parent / "test_channel_purpose_gates.py"
    _gates_spec = importlib.util.spec_from_file_location("_leg_c_gates", _gates_path)
    assert _gates_spec is not None and _gates_spec.loader is not None
    _gates_mod = importlib.util.module_from_spec(_gates_spec)
    _gates_spec.loader.exec_module(_gates_mod)
    baseline_objective = str(_gates_mod.BASELINE_INTAKE["objective"])
    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _StepFactCeilingFinal(),
            _Ctx(
                {
                    "assemble": _Out(
                        {
                            "pin_ledger": _LEG_C_PIN_LEDGER,
                            "channel": "email",
                            "recipient": "AABHearing@cob.sccgov.org",
                            "objective": baseline_objective,
                            "intake": {
                                "recipient": "AABHearing@cob.sccgov.org",
                                "signer": "Kaywan Joseph Mansubi",
                                "objective": baseline_objective,
                            },
                        }
                    ),
                    "draft": _Out(
                        {
                            "ok": True,
                            "draft": _LEG_C_V1_EMAIL,
                            "selected_role": "drafter_xai",
                        }
                    ),
                    "fact_ceiling_audit": _Out(
                        {
                            "findings": [],
                            "verdict": "ship",
                            "clauses": _LEG_C_V1_CLAUSES,
                        }
                    ),
                    "prose_rhythm_pass": _Raw(_LEG_C_V2_EMAIL),
                }
            ),
        )
    ).json
    assert payload is not None
    assert payload["violations"] == []
    assert payload["pass"] is True
