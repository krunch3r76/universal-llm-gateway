"""Skeptic-pass hard gate for material (judgment_required) implement admission."""

from __future__ import annotations

import pytest

from implement_admission.dense_spec_schema import dense_spec_hash_uri
from implement_admission.implement_ready import evaluate_implement_ready

_NOW = "2026-06-29T00:00:00+00:00"
_SPEC = "tasks/specs/sample.md"

_VALID_DENSE_SPEC = """\
# Dense test spec

## 1. Problem

A problem exists.

## 2. Non-goals / scope exclusions

Out of scope items.

## 3. Source-of-truth / provenance

| Source | Role |
|---|---|
| spec | authoritative |

## 4. Touch-point inventory

- a.py

## 5. Bound design decisions / fork table

| Fork | Decision |
|---|---|
| 1 | resolved |

## 6. Implementation guidance

Build the gate.

## 7. Acceptance criteria

1. criterion one

## 8. Verification / quality gates

- pytest green

<reasoning_trace>

No fork remains OPEN.

</reasoning_trace>
"""


def _ready_kwargs(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "todo_id": "todo:sample",
        "density_triage": "judgment_required",
        "source_uri": _SPEC,
        "implement_ready_assertion_id": 1,
        "assertion": {
            "entity_id": "todo:sample",
            "evidence_uris": [_SPEC, dense_spec_hash_uri(_VALID_DENSE_SPEC)],
        },
        "now_iso": _NOW,
        "dense_spec_uri": _SPEC,
        "dense_spec_text": _VALID_DENSE_SPEC,
        "files_expected": ["a.py"],
        "acceptance_criteria": ["criterion one"],
        "entity_name": "Sample",
    }
    base.update(over)
    return base


@pytest.mark.offline
def test_mechanical_is_exempt_from_skeptic_gate() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(density_triage="mechanical", skeptic_ratified=False)
    )
    assert verdict.admitted


@pytest.mark.offline
def test_material_without_skeptic_is_refused() -> None:
    # NOTE: this test exercises the gate ordering; if the spec/assertion checks
    # short-circuit first in your build, stub them as in _ready_kwargs so the
    # skeptic branch is reached. Expect skeptic_pass_missing once all prior
    # readiness holds.
    verdict = evaluate_implement_ready(**_ready_kwargs(skeptic_ratified=False))
    assert not verdict.admitted
    assert verdict.code == "skeptic_pass_missing"


@pytest.mark.offline
def test_material_with_skeptic_is_admitted() -> None:
    verdict = evaluate_implement_ready(**_ready_kwargs(skeptic_ratified=True))
    assert verdict.admitted


@pytest.mark.offline
def test_recon_pending_is_blocked() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(density_triage="recon_pending", skeptic_ratified=False)
    )
    assert not verdict.admitted
    assert verdict.code == "implement_blocked_recon_pending"


@pytest.mark.offline
def test_recon_waived_admits_without_skeptic() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(skeptic_ratified=False, recon_waived=True)
    )
    assert verdict.admitted


@pytest.mark.offline
def test_recon_waived_does_not_bypass_dense_spec() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=False,
            recon_waived=True,
            dense_spec_text="# Sparse\n\n## Problem\n\nOnly one section.\n",
        )
    )
    assert not verdict.admitted
    assert verdict.code == "implement_spec_not_dense"


@pytest.mark.offline
def test_recon_waived_does_not_bypass_attrs() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=False,
            recon_waived=True,
            files_expected=[],
        )
    )
    assert not verdict.admitted
    assert verdict.code == "implement_attrs_unpopulated"


@pytest.mark.offline
def test_recon_waived_does_not_bypass_recon_pending() -> None:
    # recon_pending is a stub block that precedes the skeptic gate; the
    # skeptic-only waiver must NOT admit a not-yet-densified todo (INV-5).
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            density_triage="recon_pending",
            skeptic_ratified=False,
            recon_waived=True,
        )
    )
    assert not verdict.admitted
    assert verdict.code == "implement_blocked_recon_pending"


@pytest.mark.offline
def test_grounded_skeptic_evidence_admits() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=True,
            skeptic_evidence_grounded=True,
        )
    )
    assert verdict.admitted


@pytest.mark.offline
@pytest.mark.parametrize(
    ("mode", "unresolved", "code"),
    [
        ("stamp_missing", None, "skeptic_evidence_stamp_missing"),
        ("malformed", None, "skeptic_evidence_malformed"),
        (None, ["workspaces://missing.md"], "skeptic_evidence_unresolved"),
        (None, None, "skeptic_evidence_missing"),
        ("reasoning_only", None, "skeptic_evidence_missing"),
    ],
)
def test_skeptic_evidence_reject_codes(
    mode: str | None,
    unresolved: list[str] | None,
    code: str,
) -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=True,
            skeptic_evidence_grounded=False,
            skeptic_evidence_unresolved=unresolved,
            skeptic_evidence_mode=mode,
        )
    )
    assert not verdict.admitted
    assert verdict.code == code


@pytest.mark.offline
def test_skeptic_pass_missing_reason_names_spec_sha256_requirement() -> None:
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=False,
            skeptic_unratified_reason=(
                "assertion 22900 matches predicate status(todo:sample, "
                "skeptic_ratified, current) and is active, but its evidence_uris "
                "does not contain the required spec_sha256:<hex> URI"
            ),
        )
    )
    assert not verdict.admitted
    assert verdict.code == "skeptic_pass_missing"
    assert verdict.reason is not None
    assert "spec_sha256" in verdict.reason
    assert "Unmet subcondition" in verdict.reason


class _FakeCortex:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self._items = items

    def entity_get(self, entity_id: str, **kwargs: object) -> dict[str, object]:
        return {}

    def assertion_get(self, assertion_id: int) -> dict[str, object]:
        return {}

    def assertions(self, entity_id: str, **kwargs: object) -> dict[str, object]:
        return {"items": self._items}


def _skeptic_item(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": 7,
        "entity_id": "todo:sample",
        "predicate_form": "status(todo:sample, skeptic_ratified, current)",
        "evidence_uris": ["agent-bus:1#turn-1", "spec_sha256:abc"],
        "superseded_by": None,
        "valid_until": None,
    }
    base.update(over)
    return base


@pytest.mark.offline
def test_resolver_reason_spec_hash_unavailable() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        resolve_skeptic_ratification,
    )

    outcome = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([]),
        now_iso=_NOW,
        spec_hash_uri=None,
    )
    assert not outcome.ratified
    assert outcome.reason is not None and "spec_sha256" in outcome.reason


@pytest.mark.offline
def test_resolver_reason_no_predicate_match() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        resolve_skeptic_ratification,
    )

    outcome = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([_skeptic_item(predicate_form="status(x, other, current)")]),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
    )
    assert not outcome.ratified
    assert outcome.reason is not None
    assert "no confirmed active assertion" in outcome.reason


@pytest.mark.offline
def test_resolver_reason_predicate_matched_but_inactive() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        resolve_skeptic_ratification,
    )

    outcome = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([_skeptic_item(superseded_by=99)]),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
    )
    assert not outcome.ratified
    assert outcome.reason is not None
    assert "superseded or expired" in outcome.reason


@pytest.mark.offline
def test_resolver_reason_spec_sha256_uri_absent_from_evidence() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        resolve_skeptic_ratification,
    )

    outcome = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex(
            [_skeptic_item(evidence_uris=["agent-bus:1#turn-1", "tasks/specs/x.md"])]
        ),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
    )
    assert not outcome.ratified
    assert outcome.reason is not None
    assert "spec_sha256:<hex>" in outcome.reason
    assert "spec_sha256:abc" in outcome.reason


@pytest.mark.offline
def test_resolver_ratified_exposes_matched_assertion() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        resolve_skeptic_ratification,
    )

    outcome = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([_skeptic_item()]),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
    )
    assert outcome.ratified
    assert outcome.reason is None
    assert outcome.assertion is not None and outcome.assertion["id"] == 7


@pytest.mark.offline
def test_resolver_claim_prefix_fallback_is_opt_in() -> None:
    from implement_admission.implement_ready_gate_resolve import (
        resolve_skeptic_ratification,
    )

    item = _skeptic_item(
        predicate_form=None,
        claim="status(todo:sample, skeptic_ratified, current) — ratified by skeptic",
    )
    strict = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([item]),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
    )
    assert not strict.ratified
    fallback = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([item]),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
        match_claim_prefix=True,
    )
    assert fallback.ratified


_SPEC_HASH = dense_spec_hash_uri(_VALID_DENSE_SPEC)
_GATE6_EVIDENCE_PATH = (
    "workspaces://universal-llm-gateway/libs/implement_admission/implement_ready.py"
)


def _gate6_turn_body(
    *,
    verdict: str = "RATIFY",
    spec_hash: str = _SPEC_HASH,
    evidence_path: str = _GATE6_EVIDENCE_PATH,
    extra_agent_bus: str | None = None,
) -> str:
    lines = [
        f"## Verdict: **{verdict}**",
        "",
        spec_hash,
        "",
        "FILE_EVIDENCE_PATHS:",
        evidence_path,
    ]
    if extra_agent_bus:
        lines.extend(["", f"See also {extra_agent_bus}"])
    return "\n".join(lines)


def _gate6_fetch(turns: dict[tuple[str, int], dict[str, object]]) -> object:
    def _fetch(thread: str, turn_number: int) -> dict[str, object] | None:
        return turns.get((thread, turn_number))

    return _fetch


def _implement_ready_assertion(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "entity_id": "todo:sample",
        "evidence_uris": [_SPEC, _SPEC_HASH],
    }
    base.update(over)
    return base


@pytest.mark.offline
def test_gate6_happy_path_ratifies_and_grounds() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    outcome = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert outcome.ratified
    assert outcome.evidence_grounded is True


@pytest.mark.offline
def test_gate6_non_affirmative_turn_rejects() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    outcome = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(verdict="REJECT"),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert not outcome.ratified
    assert outcome.reason is not None and "affirmative verdict" in outcome.reason


@pytest.mark.offline
def test_gate6_spec_hash_mismatch_in_turn_rejects() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    outcome = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(spec_hash="spec_sha256:stale"),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert not outcome.ratified
    assert outcome.reason is not None and "spec_sha256 token" in outcome.reason


@pytest.mark.offline
def test_gate6_unresolved_paths_fail_grounding() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    outcome = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(
                        evidence_path="workspaces://universal-llm-gateway/missing.py"
                    ),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert outcome.ratified
    assert outcome.evidence_grounded is False
    assert outcome.evidence_unresolved


@pytest.mark.offline
def test_gate6_missing_attr_rejects() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    outcome = resolve_gate6_ratification(
        todo_attrs={},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch({}),
    )
    assert not outcome.ratified
    assert outcome.reason is not None and "gate6_ratification_uri" in outcome.reason


@pytest.mark.offline
def test_gate6_designated_uri_only_not_first_implement_ready_bus() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    outcome = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(
            evidence_uris=[
                "agent-bus:9999#turn-1",
                _SPEC,
                _SPEC_HASH,
            ]
        ),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(extra_agent_bus="agent-bus:9999#turn-1"),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert outcome.ratified
    assert outcome.evidence_grounded is True


@pytest.mark.offline
def test_gate6_evaluate_implement_ready_admits() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )

    gate6 = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(),
                    "from": "reviewer",
                }
            }
        ),
    )
    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=gate6.ratified,
            skeptic_evidence_grounded=gate6.evidence_grounded,
            skeptic_evidence_unresolved=gate6.evidence_unresolved,
            skeptic_evidence_mode=gate6.evidence_mode,
        )
    )
    assert verdict.admitted


@pytest.mark.offline
def test_gate6_does_not_fallback_when_skeptic_stamp_fails_grounding() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )
    from implement_admission.implement_ready_gate_resolve import (
        SkepticRatificationOutcome,
        resolve_skeptic_ratification,
    )

    skeptic = resolve_skeptic_ratification(
        todo_id="todo:sample",
        cortex=_FakeCortex([_skeptic_item()]),
        now_iso=_NOW,
        spec_hash_uri="spec_sha256:abc",
        resolve_skeptic=lambda assertion: SkepticRatificationOutcome(
            ratified=True,
            evidence_grounded=False,
            evidence_mode="reasoning_only",
            assertion=assertion,
        ),
    )
    assert skeptic.assertion is not None
    assert skeptic.evidence_grounded is False

    gate6 = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(
            evidence_uris=[_SPEC, dense_spec_hash_uri(_VALID_DENSE_SPEC)]
        ),
        spec_hash_uri=dense_spec_hash_uri(_VALID_DENSE_SPEC),
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(
                        spec_hash=dense_spec_hash_uri(_VALID_DENSE_SPEC)
                    ),
                    "from": "reviewer",
                }
            }
        ),
    )
    assert gate6.ratified and gate6.evidence_grounded is True

    verdict = evaluate_implement_ready(
        **_ready_kwargs(
            skeptic_ratified=skeptic.ratified,
            skeptic_evidence_grounded=skeptic.evidence_grounded,
            skeptic_evidence_mode=skeptic.evidence_mode,
        )
    )
    assert not verdict.admitted
    assert verdict.code == "skeptic_evidence_missing"


@pytest.mark.offline
def test_preflight_gate6_parity_with_evaluate() -> None:
    from implement_admission.implement_ready_gate6_resolve import (
        resolve_gate6_ratification,
    )
    from implement_admission.implement_ready_preflight import preflight_implement_ready

    gate6 = resolve_gate6_ratification(
        todo_attrs={"gate6_ratification_uri": "agent-bus:5012#turn-3"},
        implement_ready_assertion=_implement_ready_assertion(),
        spec_hash_uri=_SPEC_HASH,
        fetch_bus_turn=_gate6_fetch(
            {
                ("5012", 3): {
                    "body": _gate6_turn_body(),
                    "from": "reviewer",
                }
            }
        ),
    )
    kwargs = _ready_kwargs(
        skeptic_ratified=gate6.ratified,
        skeptic_evidence_grounded=gate6.evidence_grounded,
        skeptic_evidence_unresolved=gate6.evidence_unresolved,
        skeptic_evidence_mode=gate6.evidence_mode,
    )
    verdict = evaluate_implement_ready(**kwargs)
    report = preflight_implement_ready(**kwargs)
    assert verdict.admitted
    assert report.admitted
    assert report.gates[13].status.value == "passed"


@pytest.mark.offline
def test_skeptic_pass_missing_reason_names_gate6_alternate() -> None:
    verdict = evaluate_implement_ready(**_ready_kwargs(skeptic_ratified=False))
    assert not verdict.admitted
    assert verdict.code == "skeptic_pass_missing"
    assert verdict.reason is not None
    assert "gate6_ratification_uri" in verdict.reason
    assert "recon_waived" in verdict.reason
