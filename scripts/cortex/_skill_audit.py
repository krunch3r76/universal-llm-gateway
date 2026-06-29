"""Audit helpers for agent_skill projections and cross-surface parity."""

from __future__ import annotations

import sys
from pathlib import Path

from _skill_constants import _SUPPRESSED, _WS
from _skill_drift import _drifts
from _skill_projection import _entity_get, _request
from _skill_scan import _scan_cortex_sot_declared, cortex_sot_slugs

# intended-single-surface skills (cortex-only domain/web skills or stub-only);
# add slugs here to suppress steady-state parity warnings.
_PARITY_ALLOWLIST: frozenset[str] = frozenset(
    {
        # Bucket B — web/dispatch-substrate single-surface
        "claude-web-boot",  # web-only boot sequence
        "claudeburst-shadow-ops",  # web-only shadow ops
        "grok-build-dispatch",  # grok-build harness (retired)
        "grokbuild-v1",  # grokbuild v1 (retired harness)
        "grokbuild-v2",  # grokbuild v2 (retired harness)
        "grok-web-dispatch",  # grok web dispatch (web-only)
        "jupiter-browser-via-mcp",  # browser automation via web MCP
        "lead-agent-git-integration",  # arc worktree Lane B (web/API only)
        "mode-b-web-orchestrator",  # web orchestrator (web-only)
        "model-tier-awareness-web",  # web-seat tier awareness (cursor has own rule)
        "web-transcript-preprocessing",  # web-only transcript pre-processing
        "xai-mcp-calling-shape",  # xAI-specific MCP calling shape (web/grok only)
        # Bucket B — retired
        "agent-build",  # RETIRED 2026-06-16 (grok-build/cursor-build removed)
        # Bucket C — domain skills (legal / finance / tax / document)
        "boe19p-appeal-discipline",
        "case-evidence-retrieval",
        "chase-escrow-discipline",
        "chase-escrow-statement-ingestion",
        "crypto-trading-research",
        "document-ingestion",
        "document-lifecycle-tracking",
        "document-review-timeline-linkage-audit",
        "docx_ingestion",
        "email-bridge-mailbox",
        "email-tool-dispatch",
        "engagement-stance",
        "financial-reasoning",
        "flintridge-case-navigation",
        "lawyer-stance",
        "legal-opinion-corpus-ingestion",
        "named-entity-verification-gate",  # legal/regulatory artifact gate
        "review-protocol-mandatory-chronology-verification",  # legal document review
        "srm",  # legal document section rewrite
        "tax",
        "w2-ingestion",
        # Bucket C — project arc (historical working notebook, not canonical)
        "cortex-v24-implementation-arc",  # temporary working notebook, sunset when Phase 3 ships
        # Bucket C — deprecated (superseded by skill-document-writing v3.0)
        "skill-authoring",
        # Bucket D — workspace-only stubs (source_uri = workspaces://; no cortex SOT file)
        "add-mcp-tool",
        "agent-bus-multitask",
        "agent-guidance-writing",
        "build-pipeline",
        "delegate-to-grok",  # RETIRED — stub preserved for historical context
        "git-posture",
        "handoff-packet-authoring",
        "multi-model-review",
        "produce-uml",
        "refine-pipeline",
        "required-skills-pickup",
        "research-article-ingest",
        "research-article-search",
        "review-task-guidance",
        "service-lifecycle",
        "skill-suggest-utilization",
        "web-generate-substrate",
    }
)


def _audit_parity(scanned: dict[str, dict[str, object]]) -> list[str]:
    cortex_slugs = cortex_sot_slugs()
    stub_slugs = set(scanned)
    cortex_only = sorted(cortex_slugs - stub_slugs - _PARITY_ALLOWLIST)
    stub_only = sorted(stub_slugs - cortex_slugs - _PARITY_ALLOWLIST)
    out: list[str] = []
    for slug in cortex_only:
        out.append(f"parity: agent_skill:{slug} cortex-SOT-only (no .cursor stub)")
    for slug in stub_only:
        out.append(f"parity: agent_skill:{slug} .cursor-stub-only (no cortex SOT)")
    return out


def _audit_terms(client: object, scanned: dict[str, dict[str, object]]) -> int:
    _ = scanned
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        print(
            f"AUDIT-TERMS FAIL: GET /entities?type=agent_skill {status}",
            file=sys.stderr,
        )
        return 2
    empty: list[str] = []
    for stub in body.get("items", []):
        entity_id = str(stub.get("id") or "")
        if not entity_id.startswith("agent_skill:"):
            continue
        get_status, live = _entity_get(client, entity_id)
        if get_status != 200:
            print(
                f"AUDIT-TERMS FAIL: GET /entities/{entity_id} {get_status}",
                file=sys.stderr,
            )
            return 2
        if live.get("lifecycle") in _SUPPRESSED:
            continue
        attrs = live.get("attributes") or {}
        terms = attrs.get("trigger_match_terms") if isinstance(attrs, dict) else None
        if not isinstance(terms, list) or not terms:
            empty.append(entity_id)
    print(
        f"Audit-terms: {len(empty)} active agent_skill(s) with empty trigger_match_terms"
    )
    for eid in sorted(empty):
        print(f"  - {eid}")
    return 0 if not empty else 1


def _audit(client: object, scanned: dict[str, dict[str, object]], root: Path) -> int:
    status, body = _request(client, "GET", "/entities?type=agent_skill&limit=500")
    if status != 200:
        print(f"AUDIT FAIL: GET /entities?type=agent_skill {status}", file=sys.stderr)
        return 2
    live_by_id = {row["id"]: row for row in body.get("items", [])}
    cortex_declared = _scan_cortex_sot_declared()
    drifted = _drifts(client, scanned, live_by_id, cortex_declared=cortex_declared)
    file_gone = [
        eid
        for eid, row in live_by_id.items()
        if eid not in {f"agent_skill:{s}" for s in scanned}
        and row.get("lifecycle") not in _SUPPRESSED
        and str(row.get("source_uri") or "").startswith(f"{_WS}/.cursor/skills/")
        and not (root / str(row["source_uri"]).removeprefix(f"{_WS}/")).is_file()
    ]
    print("Audit: agent_skill filesystem projections")
    print(f"  Scanned workspace skills : {len(scanned)}")
    print(f"  Drifted projections      : {len(drifted)}")
    for line in drifted:
        print(f"    - {line}")
    print(f"  File-gone (report only)  : {len(file_gone)}")
    for eid in sorted(file_gone):
        print(f"    - {eid}")
    parity = _audit_parity(scanned)
    print(f"  Parity gaps (report only) : {len(parity)}")
    for line in parity:
        print(f"    - {line}")
    return 0 if not drifted else 1
