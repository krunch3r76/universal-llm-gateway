"""Per-coat static checks for the unearned-self-assertion reporter.

Each function returns a CoatResult that either names a derived denominator
or refuses with could_not_check. Hand-lists are always diffed against a
derived candidate set so list staleness is visible to the reporter itself.
"""

from __future__ import annotations

import json
from pathlib import Path

from contract_vocab import vision_required_contracts
from unearned_self_assertion_auditor.extract import (
    disclosure_candidates,
    extract_frozenset_assign,
    prose_omits_enforcement_members,
)
from unearned_self_assertion_auditor.report import CoatResult

_WRITE_OP_FILES = (
    "services/git_integration_worker/cursor_sdk_observed_reconcile.py",
    "services/git_integration_worker/cursor_sdk_cortex_identity.py",
    "services/git_integration_worker/cursor_sdk_manifest.py",
)
_DISCLOSURE_HAND_LIST = (
    "services/mcp-server/tools/cursor_request.py",
    "services/mcp-server/tools/agent_bus/__init__.py",
    "services/mcp-server/tools/agent_bus/request.py",
    "services/mcp-server/tools/_oc_knowledge_templates.py",
    "libs/claude_bundles/operator_proxy_tier_m.py",
)
_IDLE_SNAPSHOT = Path(
    "/mnt/torus/mcp-data/files/notes/system/operational/fleet-idle-gate-observation.json"
)


def coat_one_enforcement(_repo: Path) -> CoatResult:
    """Return how many contracts require vision, derived from the live records helper."""
    members = sorted(vision_required_contracts())
    return CoatResult(
        coat_id="coat_one_enforcement",
        verdict="checked_and_found_nothing",
        denominator_kind="derived",
        denominator_source="contract_vocab.vision_required_contracts()",
        denominator_count=len(members),
        coverage_rest=(
            "len(vision_required_contracts()) is the enforcement-set "
            f"denominator; members={members}"
        ),
        notes=["RECORDS completeness is CI-pinned, not reporter-emitted"],
    )


def coat_one_disclosure(repo: Path) -> CoatResult:
    """Diff vision_required members against a hand-list, then detect list staleness."""
    listed = set(_DISCLOSURE_HAND_LIST)
    candidates = disclosure_candidates(repo)
    unmatched = sorted(candidates - listed)
    findings: list[str] = []
    for rel in _DISCLOSURE_HAND_LIST:
        findings.extend(prose_omits_enforcement_members(repo, rel))
    if unmatched:
        return CoatResult(
            coat_id="coat_one_disclosure",
            verdict="could_not_check",
            denominator_kind="hand_list",
            denominator_source="DISCLOSURE_HAND_LIST vs derived vision-marker files",
            denominator_count=len(listed),
            coverage_rest=(
                "hand-list is stale relative to derived candidates; "
                "cannot claim complete disclosure coverage"
            ),
            findings=findings,
            notes=[f"unmatched_candidates={unmatched}"],
        )
    if findings:
        return CoatResult(
            coat_id="coat_one_disclosure",
            verdict="finding",
            denominator_kind="hand_list",
            denominator_source="DISCLOSURE_HAND_LIST (candidate-set agreed)",
            denominator_count=len(listed),
            coverage_rest="listed sites cover the derived candidate set",
            findings=findings,
        )
    return CoatResult(
        coat_id="coat_one_disclosure",
        verdict="checked_and_found_nothing",
        denominator_kind="hand_list",
        denominator_source="DISCLOSURE_HAND_LIST (candidate-set agreed)",
        denominator_count=len(listed),
        coverage_rest="listed sites cover derived candidates and disclose all members",
    )


def coat_two_schema(repo: Path) -> CoatResult:
    """Confirm the per-record search-executed field exists; fleet collapse stays a later coat."""
    models = repo / "libs/unclaimed_property_hunter/models.py"
    if not models.is_file():
        return CoatResult(
            coat_id="coat_two_schema",
            verdict="could_not_check",
            denominator_kind="none",
            denominator_source="libs/unclaimed_property_hunter/models.py",
            denominator_count=None,
            coverage_rest="RunRecord source missing",
        )
    text = models.read_text(encoding="utf-8")
    has_field = "search_executed: bool" in text
    return CoatResult(
        coat_id="coat_two_schema",
        verdict="finding" if not has_field else "checked_and_found_nothing",
        denominator_kind="schema_presence",
        denominator_source="RunRecord.search_executed",
        denominator_count=1,
        coverage_rest=(
            "per-record reason field present; fleet-wide aggregator collapse "
            "is a separate coat with no closed consumer set"
        ),
        findings=[] if has_field else ["RunRecord.search_executed missing"],
    )


def coat_two_aggregators(_repo: Path) -> CoatResult:
    """Refuse a fleet 'no collapse' claim without a named consumer corpus."""
    return CoatResult(
        coat_id="coat_two_aggregators",
        verdict="could_not_check",
        denominator_kind="named_corpus",
        denominator_source="none — no closed aggregator set in the tree",
        denominator_count=None,
        coverage_rest=(
            "a clean fleet claim requires a named consumer corpus; "
            "absence of that corpus is could_not_check, not zero collapse"
        ),
    )


def coat_three_write_ops(repo: Path) -> CoatResult:
    """Diff the three _CORTEX_WRITE_OPS frozensets and treat disagreement as a finding."""
    extracted: dict[str, set[str] | None] = {}
    for rel in _WRITE_OP_FILES:
        extracted[rel] = extract_frozenset_assign(repo / rel, "_CORTEX_WRITE_OPS")
    missing = [rel for rel, value in extracted.items() if value is None]
    if missing:
        return CoatResult(
            coat_id="coat_three_write_ops",
            verdict="could_not_check",
            denominator_kind="derived",
            denominator_source="AST _CORTEX_WRITE_OPS in three GIW modules",
            denominator_count=None,
            coverage_rest="could not parse one or more frozensets",
            notes=[f"unparsed={missing}"],
        )
    values = list(extracted.values())
    assert all(v is not None for v in values)
    unique = {frozenset(v) for v in values if v is not None}
    findings: list[str] = []
    if len(unique) > 1:
        for rel, members in extracted.items():
            findings.append(f"{rel}: {sorted(members or ())}")
    return CoatResult(
        coat_id="coat_three_write_ops",
        verdict="finding" if findings else "checked_and_found_nothing",
        denominator_kind="derived",
        denominator_source="AST _CORTEX_WRITE_OPS ×3",
        denominator_count=3,
        coverage_rest="three static tables; membership disagreement is a finding",
        findings=findings,
    )


def coat_three_fleet(_repo: Path) -> CoatResult:
    """Refuse a fleet-wide coat-three clean without a named closeout corpus."""
    return CoatResult(
        coat_id="coat_three_fleet",
        verdict="could_not_check",
        denominator_kind="named_corpus",
        denominator_source="none — conversation cortex absence=unknown",
        denominator_count=None,
        coverage_rest=(
            "absence of divergence tokens on an absence=unknown surface "
            "is not a zero; name a closeout corpus to check"
        ),
    )


def coat_four_absence_schema(repo: Path) -> CoatResult:
    """thread_get omits cursor_auto_job when the worker is unreachable — ambiguous."""
    path = repo / "services/mcp-server/tools/agent_bus/threads.py"
    if not path.is_file():
        return CoatResult(
            coat_id="coat_four_absence_schema",
            verdict="could_not_check",
            denominator_kind="none",
            denominator_source="threads.py",
            denominator_count=None,
            coverage_rest="enrich helper missing",
        )
    text = path.read_text(encoding="utf-8")
    omits = "Worker unreachable → omit the field" in text
    has_liveness_field = "liveness" in text and "cursor_auto_job" in text
    if omits and not has_liveness_field:
        return CoatResult(
            coat_id="coat_four_absence_schema",
            verdict="finding",
            denominator_kind="schema_presence",
            denominator_source="threads._enrich_with_cursor_auto_job",
            denominator_count=1,
            coverage_rest=(
                "read path has no liveness field; omitted cursor_auto_job "
                "cannot distinguish no-job from down-worker"
            ),
            findings=[
                "thread_get omits cursor_auto_job on worker unreachable "
                "(threads.py:142); no read-side liveness discriminator"
            ],
        )
    return CoatResult(
        coat_id="coat_four_absence_schema",
        verdict="checked_and_found_nothing",
        denominator_kind="schema_presence",
        denominator_source="threads._enrich_with_cursor_auto_job",
        denominator_count=1,
        coverage_rest="read path carries a liveness discriminator or does not omit",
    )


def coat_five_schedule(_repo: Path) -> CoatResult:
    """send_later is not in this tree; fire_at accuracy cannot be checked here."""
    return CoatResult(
        coat_id="coat_five_schedule",
        verdict="could_not_check",
        denominator_kind="none",
        denominator_source="send_later return schema — not in universal-llm-gateway",
        denominator_count=None,
        coverage_rest=(
            "no in-repo send_later implementation; accuracy semantics "
            "cannot be derived from this tree"
        ),
    )


def coat_positive_staleness(_repo: Path) -> CoatResult:
    """Protect dated+disclosed-staleness instruments; a stripped rule is a predicate hit."""
    if not _IDLE_SNAPSHOT.is_file():
        return CoatResult(
            coat_id="coat_positive_staleness",
            verdict="could_not_check",
            denominator_kind="none",
            denominator_source=str(_IDLE_SNAPSHOT),
            denominator_count=None,
            coverage_rest="idle-gate snapshot unreadable",
        )
    payload = json.loads(_IDLE_SNAPSHOT.read_text(encoding="utf-8"))
    rule = payload.get("staleness_rule")
    dated = payload.get("pass_at_utc")
    if rule and dated:
        return CoatResult(
            coat_id="coat_positive_staleness",
            verdict="checked_and_found_nothing",
            denominator_kind="schema_presence",
            denominator_source="fleet-idle-gate-observation.json",
            denominator_count=1,
            coverage_rest="dated snapshot still carries a disclosed staleness_rule",
            notes=[f"pass_at_utc={dated}"],
        )
    return CoatResult(
        coat_id="coat_positive_staleness",
        verdict="finding",
        denominator_kind="schema_presence",
        denominator_source="fleet-idle-gate-observation.json",
        denominator_count=1,
        coverage_rest="safeguard stripped — dated instrument without staleness_rule",
        findings=["staleness_rule or pass_at_utc missing"],
    )


def all_coats(repo: Path) -> list[CoatResult]:
    """Run the closed coat set. The list length is the reporter's own denominator."""
    return [
        coat_one_enforcement(repo),
        coat_one_disclosure(repo),
        coat_two_schema(repo),
        coat_two_aggregators(repo),
        coat_three_write_ops(repo),
        coat_three_fleet(repo),
        coat_four_absence_schema(repo),
        coat_five_schedule(repo),
        coat_positive_staleness(repo),
    ]
