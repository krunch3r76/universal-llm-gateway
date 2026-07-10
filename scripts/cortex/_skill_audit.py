"""Audit helpers for agent_skill projections and cross-surface parity."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from _skill_constants import (
    _SUPPRESSED,
    _WS,
    ALLOWLIST_METADATA_KEYS,
    paired_rule_exists,
)
from _skill_drift import _drifts
from _skill_projection import _entity_get, _request
from _skill_render import extract_renderer_fields
from _skill_scan import _scan_cortex_sot_declared, _scan_skills, cortex_sot_slugs

VerdictStatus = Literal["clean", "dirty", "error", "warnings"]


def _meta(
    reason: str,
    *,
    owner: str = "platform",
    expiry: str = "2099-12-31",
    directionality: str = "cortex-only",
    temporary: str = "structural",
) -> dict[str, str]:
    return {
        "reason": reason,
        "owner": owner,
        "expiry_or_assertion_ref": expiry,
        "directionality": directionality,
        "temporary_or_structural": temporary,
    }


# intended-single-surface skills (cortex-only domain/web skills or stub-only).
_PARITY_ALLOWLIST: dict[str, dict[str, str]] = {
    "claude-web-boot": _meta("web-only boot sequence"),
    "claudeburst-shadow-ops": _meta("web-only shadow ops"),
    "grok-build-dispatch": _meta("grok-build harness (retired)", temporary="temporary"),
    "grokbuild-v1": _meta("grokbuild v1 (retired harness)", temporary="temporary"),
    "grokbuild-v2": _meta("grokbuild v2 (retired harness)", temporary="temporary"),
    "grok-web-dispatch": _meta("grok web dispatch (web-only)"),
    "jupiter-browser-via-mcp": _meta("browser automation via web MCP"),
    "lead-agent-git-integration": _meta("arc worktree Lane B (web/API only)"),
    "mode-b-web-orchestrator": _meta("web orchestrator (web-only)"),
    "model-tier-awareness-web": _meta("web-seat tier awareness (cursor has own rule)"),
    "web-transcript-preprocessing": _meta("web-only transcript pre-processing"),
    "agent-build": _meta(
        "RETIRED 2026-06-16 (grok-build/cursor-build removed)",
        temporary="temporary",
    ),
    "boe19p-appeal-discipline": _meta(
        "RETIRED 2026-07-02 → document:boe19p-appeal-discipline (has_playbook)"
    ),
    "case-evidence-retrieval": _meta("legal domain skill"),
    "chase-escrow-discipline": _meta(
        "RETIRED 2026-07-02 → document:chase-escrow-discipline (has_playbook)"
    ),
    "chase-escrow-statement-ingestion": _meta(
        "RETIRED 2026-07-02 — folded into chase-escrow playbook"
    ),
    "crypto-trading-research": _meta("finance domain skill"),
    "document-ingestion": _meta("document domain skill"),
    "document-lifecycle-tracking": _meta("document domain skill"),
    "document-review-timeline-linkage-audit": _meta("legal domain skill"),
    "docx-ingestion": _meta("document domain skill"),
    "email-bridge-mailbox": _meta("email bridge domain skill"),
    "email-tool-dispatch": _meta("email bridge domain skill"),
    "engagement-stance": _meta("legal domain skill"),
    "financial-reasoning": _meta("finance domain skill"),
    "flintridge-case-navigation": _meta(
        "RETIRED 2026-07-02 → document:flintridge-case-navigation (has_playbook)"
    ),
    "hei-application-discipline": _meta(
        "RETIRED 2026-07-02 → document:hei-discipline (archived case)"
    ),
    "matter-discipline-pattern": _meta("universal case-playbook loader pattern"),
    "lawyer-stance": _meta("legal domain skill"),
    "legal-opinion-corpus-ingestion": _meta("legal domain skill"),
    "named-entity-verification-gate": _meta("legal/regulatory artifact gate"),
    "review-protocol-mandatory-chronology-verification": _meta("legal document review"),
    "srm": _meta("legal document section rewrite"),
    "tax": _meta("tax domain skill"),
    "w2-ingestion": _meta("tax domain skill"),
    "cortex-v24-implementation-arc": _meta(
        "temporary working notebook, sunset when Phase 3 ships",
        temporary="temporary",
    ),
    "skill-authoring": _meta(
        "deprecated (superseded by skill-document-writing v3.0)",
        temporary="temporary",
    ),
    "add-mcp-tool": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "agent-bus-multitask": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "agent-guidance-writing": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "build-pipeline": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "git-posture": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "handoff-packet-authoring": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "multi-model-review": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "produce-uml": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "refine-pipeline": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "required-skills-pickup": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "research-article-ingest": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "research-article-search": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "review-task-guidance": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "service-lifecycle": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "skill-suggest-utilization": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
    "web-generate-substrate": _meta(
        "workspace-only stub (no cortex SOT file)", directionality="stub-only"
    ),
}


def _allowlist_keys() -> set[str]:
    return set(_PARITY_ALLOWLIST)


def _parse_expiry(raw: str) -> date | None:
    text = raw.strip()
    if not text or text.startswith("assertion:"):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def allowlist_verdict() -> tuple[VerdictStatus, list[str]]:
    warnings: list[str] = []
    errors: list[str] = []
    today = datetime.now(UTC).date()
    for slug, meta in sorted(_PARITY_ALLOWLIST.items()):
        missing = [
            key
            for key in ALLOWLIST_METADATA_KEYS
            if not str(meta.get(key) or "").strip()
        ]
        if missing:
            warnings.append(f"{slug}: missing metadata {missing}")
        expiry = _parse_expiry(str(meta.get("expiry_or_assertion_ref") or ""))
        if expiry is not None and expiry < today:
            errors.append(f"{slug}: allowlist entry expired ({expiry.isoformat()})")
    if errors:
        return "error", errors
    if warnings:
        return "dirty", warnings
    return "clean", []


def _missing_stub_critical(
    slug: str,
    fields: dict[str, object],
    *,
    repo_root: Path,
) -> list[str]:
    missing: list[str] = []
    for key in ("description", "trigger_match_terms", "source_uri"):
        value = fields.get(key)
        if key == "trigger_match_terms":
            if not isinstance(value, list) or not value:
                missing.append(key)
            continue
        if not str(value or "").strip():
            missing.append(key)
    if (
        paired_rule_exists(slug, repo_root)
        and not str(fields.get("paired_rule_pointer") or "").strip()
    ):
        missing.append("paired_rule_pointer")
    return missing


def stub_critical_field_verdict(
    client: object,
    repo_root: Path,
) -> tuple[VerdictStatus, list[str], set[str]]:
    """Return verdict, problem lines, and slugs blocked from stub generation."""
    from _skill_manifest import fetch_discoverable_entities

    blocked: set[str] = set()
    problems: list[str] = []
    try:
        entities = fetch_discoverable_entities(client)
    except RuntimeError as exc:
        return "error", [str(exc)], blocked
    allowlist = _allowlist_keys()
    for slug, entity in sorted(entities.items()):
        if slug in allowlist:
            continue
        fields = extract_renderer_fields(entity, slug)
        missing = _missing_stub_critical(slug, fields, repo_root=repo_root)
        if missing:
            blocked.add(slug)
            problems.append(
                f"agent_skill:{slug} missing stub-critical field(s): {missing}"
            )
    if problems:
        return "error", problems, blocked
    return "clean", [], blocked


def parity_verdict(
    scanned: dict[str, dict[str, object]],
    repo_root: Path | None = None,
) -> tuple[VerdictStatus, list[str]]:
    root = repo_root or Path(__file__).resolve().parent.parent.parent
    lines = _audit_parity(scanned, root)
    if lines:
        return "dirty", lines
    return "clean", []


def _audit_parity(scanned: dict[str, dict[str, object]], repo_root: Path) -> list[str]:
    cortex_slugs = cortex_sot_slugs(repo_root)
    stub_slugs = set(scanned)
    allowlist = _allowlist_keys()
    cortex_only = sorted(cortex_slugs - stub_slugs - allowlist)
    stub_only = sorted(stub_slugs - cortex_slugs - allowlist)
    out: list[str] = []
    for slug in cortex_only:
        out.append(f"parity: agent_skill:{slug} cortex-SOT-only (no .cursor stub)")
    for slug in stub_only:
        out.append(f"parity: agent_skill:{slug} .cursor-stub-only (no cortex SOT)")
    return out


def _fetch_guidance_entity_stubs(client: object) -> tuple[int, list[dict[str, object]]]:
    """List entity stubs across agent_skill + rule + skill."""
    merged: list[dict[str, object]] = []
    for entity_type in ("agent_skill", "rule", "skill"):
        status, body = _request(
            client, "GET", f"/entities?type={entity_type}&limit=500"
        )
        if status != 200:
            return status, []
        merged.extend(body.get("items", []))
    return 200, merged


def _audit_terms(client: object, scanned: dict[str, dict[str, object]]) -> int:
    _ = scanned
    status, stubs = _fetch_guidance_entity_stubs(client)
    if status != 200:
        print(
            f"AUDIT-TERMS FAIL: GET /entities guidance types {status}",
            file=sys.stderr,
        )
        return 2
    empty: list[str] = []
    for stub in stubs:
        entity_id = str(stub.get("id") or "")
        if ":" not in entity_id:
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
    status, stubs = _fetch_guidance_entity_stubs(client)
    if status != 200:
        print(f"AUDIT FAIL: GET /entities guidance types {status}", file=sys.stderr)
        return 2
    live_by_id = {row["id"]: row for row in stubs}
    cortex_declared = _scan_cortex_sot_declared(root)
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
    parity = _audit_parity(scanned, root)
    print(f"  Parity gaps (report only) : {len(parity)}")
    for line in parity:
        print(f"    - {line}")
    return 0 if not drifted else 1


def scan_workspace_stubs(root: Path) -> dict[str, dict[str, object]]:
    return _scan_skills(root.resolve())
