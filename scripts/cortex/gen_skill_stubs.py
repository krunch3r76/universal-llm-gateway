#!/usr/bin/env python3
"""Generate and verify ``.cursor/skills/`` stubs from cortex agent_skill SOT."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
_SCRIPTS_CORTEX = Path(__file__).resolve().parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))
if str(_SCRIPTS_CORTEX) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_CORTEX))

from _skill_audit import (  # noqa: E402
    _PARITY_ALLOWLIST,
    allowlist_verdict,
    parity_verdict,
    stub_critical_field_verdict,
)
from claude_bundles.resolver import (  # noqa: E402
    CURSOR_INDEXED_SLUGS,
)

_CURSOR_PRIMARY_SLUGS = set(CURSOR_INDEXED_SLUGS)
from _skill_constants import (  # noqa: E402
    _SOT_DRIFT_HOLDOUTS,
    _SOT_DRIFT_KNOWN_RESIDUALS,
    _SUPPRESSED,
    REMEDIATION_CMD,
)
from _skill_drift import _drifts  # noqa: E402
from _skill_manifest import (  # noqa: E402
    aggregate_verdicts,
    build_manifest_payload,
    build_renderer_snapshot,
    generator_manifest_verdict,
    read_manifest,
    scanned_stub_slugs,
    verify_manifest,
    write_manifest,
)
from _skill_render import extract_renderer_fields, render_stub  # noqa: E402
from _skill_scan import _scan_cortex_sot_declared, _scan_skills  # noqa: E402
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

VERDICT_ORDER = (
    "EDGE_DRIFT",
    "SOT_DRIFT",
    "STUB_PARITY",
    "GENERATOR_MANIFEST",
    "ALLOWLIST",
)


def _edge_drift_verdict(client: object, repo_root: Path) -> tuple[str, list[str]]:
    scanned = _scan_skills(repo_root.resolve())
    cortex_declared = _scan_cortex_sot_declared()
    drifted = _drifts(client, scanned, cortex_declared=cortex_declared)
    if drifted:
        return "dirty", drifted
    return "clean", []


def _is_cortex_tier_source_uri(source_uri: str) -> bool:
    uri = source_uri.strip()
    return uri.startswith("agent-skills/") or uri.startswith("cortex://agent-skills/")


def _sot_drift_verdict(client: object, repo_root: Path) -> tuple[str, list[str]]:
    _, entities = build_renderer_snapshot(client)
    skills_dir = repo_root / ".cursor" / "skills"
    dirty_lines: list[str] = []
    info_lines: list[str] = []
    for slug, entity in sorted(entities.items()):
        if entity.get("type") != "agent_skill":
            continue
        if entity.get("lifecycle") in _SUPPRESSED:
            continue
        if slug in _SOT_DRIFT_HOLDOUTS:
            continue
        fields = extract_renderer_fields(entity, slug)
        source_uri = str(fields.get("source_uri") or entity.get("source_uri") or "")
        entity_id = str(entity.get("id") or f"agent_skill:{slug}")
        rules_fired: list[str] = []
        if _is_cortex_tier_source_uri(source_uri):
            rules_fired.append("A cortex-tier source_uri")
        if not (skills_dir / slug / "SKILL.md").is_file():
            rules_fired.append("B missing .cursor twin")
        if not rules_fired:
            continue
        rule_str = " / ".join(rules_fired)
        hint = (
            f"migrate body to `.cursor/skills/{slug}/SKILL.md` SOT + repoint "
            f"source_uri; then `{REMEDIATION_CMD}`"
        )
        line = f"{entity_id}: {rule_str} — {hint}"
        if slug in _SOT_DRIFT_KNOWN_RESIDUALS:
            info_lines.append(f"KNOWN-RESIDUAL: {line}")
        else:
            dirty_lines.append(line)
    lines = dirty_lines + info_lines
    if dirty_lines:
        return "dirty", lines
    return "clean", lines


def run_check(
    client: object, repo_root: Path, *, gate_manifest: bool = True
) -> int:
    scanned = scanned_stub_slugs(repo_root)
    edge_status, edge_lines = _edge_drift_verdict(client, repo_root)
    sot_status, sot_lines = _sot_drift_verdict(client, repo_root)
    critical_status, critical_lines, _ = stub_critical_field_verdict(client, repo_root)
    parity_status, parity_lines = parity_verdict(scanned)
    if critical_status == "error":
        stub_status = "error"
        stub_lines = critical_lines + parity_lines
    elif parity_status == "dirty":
        stub_status = "dirty"
        stub_lines = parity_lines
    else:
        stub_status = "clean"
        stub_lines = []
    manifest_status, manifest_lines = generator_manifest_verdict(repo_root, client)
    allow_status, allow_lines = allowlist_verdict()
    verdicts = aggregate_verdicts(
        edge_drift=edge_status,  # type: ignore[arg-type]
        sot_drift=sot_status,  # type: ignore[arg-type]
        stub_parity=stub_status,  # type: ignore[arg-type]
        generator_manifest=manifest_status,  # type: ignore[arg-type]
        allowlist=allow_status,  # type: ignore[arg-type]
    )
    for name in VERDICT_ORDER:
        print(f"{name}: {verdicts[name]}")
    detail_map = {
        "EDGE_DRIFT": edge_lines,
        "SOT_DRIFT": sot_lines,
        "STUB_PARITY": stub_lines,
        "GENERATOR_MANIFEST": manifest_lines,
        "ALLOWLIST": allow_lines,
    }
    for name in VERDICT_ORDER:
        for line in detail_map[name]:
            print(f"  {name}: {line}", file=sys.stderr)
    gated_verdicts = (
        VERDICT_ORDER
        if gate_manifest
        else tuple(name for name in VERDICT_ORDER if name != "GENERATOR_MANIFEST")
    )
    failed = any(verdicts[name] in {"dirty", "error"} for name in gated_verdicts)
    return 1 if failed else 0


def _write_stub(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def run_generate(client: object, repo_root: Path) -> int:
    critical_status, critical_lines, blocked = stub_critical_field_verdict(
        client, repo_root
    )
    if critical_status == "error":
        for line in critical_lines:
            print(f"GENERATE FAIL: {line}", file=sys.stderr)
        return 1
    _, entities = build_renderer_snapshot(client)
    allowlist = set(_PARITY_ALLOWLIST)
    skills_dir = repo_root / ".cursor" / "skills"
    generated = 0
    skipped_allowlist = sorted(allowlist)
    changed = False
    for slug in sorted(entities):
        if slug in allowlist or slug in blocked or slug in _CURSOR_PRIMARY_SLUGS:
            continue
        fields = extract_renderer_fields(entities[slug], slug)
        content = render_stub(slug, fields)
        stub_path = skills_dir / slug / "SKILL.md"
        if _write_stub(stub_path, content):
            changed = True
        generated += 1
    check_code = run_check(client, repo_root, gate_manifest=False)
    if check_code != 0:
        print(
            "GENERATE FAIL: post-generation check failed — manifest not written",
            file=sys.stderr,
        )
        return check_code
    payload = build_manifest_payload(
        repo_root=repo_root,
        client=client,
        generated_count=generated,
        skipped_allowlist=skipped_allowlist,
    )
    existing = read_manifest(repo_root)
    if existing != payload:
        write_manifest(repo_root, payload)
        changed = True
    if not changed:
        print("OK gen_skill_stubs --generate (no-op)")
    else:
        print(f"OK gen_skill_stubs --generate ({generated} stub(s))")
    return 0


def run_verify_manifest(client: object, repo_root: Path) -> int:
    status, problems = verify_manifest(repo_root, client)
    if status == "clean":
        print("OK gen_skill_stubs --verify-manifest")
        return 0
    for line in problems:
        print(f"VERIFY-MANIFEST: {line}", file=sys.stderr)
    print(f"Remediation: {REMEDIATION_CMD}", file=sys.stderr)
    return 1


def skill_graph_staleness_cue(repo_root: Path, client: object) -> str | None:
    """Boot/orientation cue — manifest-hash drift via ``verify_manifest()``."""
    status, problems = verify_manifest(repo_root, client)
    if status == "clean":
        return None
    detail = "; ".join(problems) if problems else "manifest drift"
    return (
        "Skill graph projection stale "
        f"({detail}). Run `{REMEDIATION_CMD}` before relying on `.cursor/skills/`."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--verify-manifest", action="store_true")
    parser.add_argument("--root", type=Path, default=_REPO)
    args = parser.parse_args(argv)
    if sum(bool(x) for x in (args.generate, args.check, args.verify_manifest)) != 1:
        print(
            "ERROR: specify exactly one of --generate, --check, --verify-manifest",
            file=sys.stderr,
        )
        return 2
    try:
        client = make_sync_client(DEFAULT_CORTEX_URL)
    except Exception as exc:
        print(f"ERROR: cortex-api unreachable: {exc}", file=sys.stderr)
        return 2
    repo_root = args.root.resolve()
    if args.check:
        return run_check(client, repo_root)
    if args.verify_manifest:
        return run_verify_manifest(client, repo_root)
    return run_generate(client, repo_root)


if __name__ == "__main__":
    sys.exit(main())
