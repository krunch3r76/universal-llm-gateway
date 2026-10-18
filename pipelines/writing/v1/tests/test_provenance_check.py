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
        {"text": "So two days of work and it's sitting where it started.", "pin_ids": ["P3"]},
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
    {"text": "Fill was 251 when I opened Saturday.", "pin_ids": ["P1"], "disposition": "express"},
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
        row["type"] == "fact_dropped" and row["detail"] == "Saturday" for row in violations
    )


def test_fact_ceiling_final_handler_derives_fact_reordered() -> None:
    v2 = (
        "It was 213 Sunday morning. "
        "Fill was 251 when I opened Saturday. "
        "So two days of work and it's sitting where it started."
    )

    class _Raw:
        def __init__(self, text: str) -> None:
            self.raw = text

    class _StepFinal:
        def __init__(self) -> None:
            self.handler_inputs = {
                "pin_ledger": "assemble.json.pin_ledger",
                "draft": "prose_rhythm_pass.raw",
                "draft_v1": "draft.json",
            }

    payload = asyncio.run(
        provenance.WritingProvenanceCheckHandler().execute(
            _StepFinal(),
            _Ctx(
                {
                    "assemble": _Out({"pin_ledger": _FILL_LEDGER}),
                    "draft": _Out(
                        {
                            "ok": True,
                            "draft": _FILL_V1,
                            "selected_role": "drafter_openai",
                        }
                    ),
                    "fact_ceiling_audit": _Out(
                        {
                            "findings": [],
                            "verdict": "ship",
                            "clauses": _FILL_V1_CLAUSES,
                        }
                    ),
                    "prose_rhythm_pass": _Raw(v2),
                }
            ),
        )
    ).json
    assert payload is not None
    assert "fact_reordered" in _types(payload["violations"])
