#!/usr/bin/env python3
"""Dry verification for lean-ladder floor + vision template staging (Leg 3 harness)."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "libs"))
sys.path.insert(0, str(_REPO_ROOT / "services" / "mcp-server"))

from agent_seat.dispatch_review_contract import (  # noqa: E402
    harvest_promoted_verdict_with_coverage,
)
from markdown_sections import _document_sections  # noqa: E402
from tools._document_manifest import (  # noqa: E402
    _assign_section_ids,
    _section_own_body,
    build_document_manifest,
)
from tools._hashing import sha256_hex_equal, sha256_hex_of_bytes  # noqa: E402

CORTEX_MOUNT = Path("/mnt/torus/mcp-data/files")
BASE_TEMPLATE_TIP = "14a9bc6206c99cafd307495adf850b3294b252433f5cb11869a1f29d3b445cc8"

DEFAULT_SAMPLE = (
    CORTEX_MOUNT / "notes/tmp/packets/lean-ladder-floor-vision-sample-preland-packet.md"
)
DEFAULT_CANDIDATE = (
    CORTEX_MOUNT
    / "notes/tmp/packets/lean-ladder-floor-vision-candidates/lean-dispatch-ladder.md"
)
DEFAULT_PLAYBOOK_CANDIDATE = (
    CORTEX_MOUNT
    / "notes/tmp/packets/lean-ladder-floor-vision-candidates/playbook-architecture-orientation.md"
)

PROTECTED_SECTION_SHA256: dict[str, str] = {
    "LEAN DISPATCH — {{slug}}/Legs (run in order; one bounded step each)/Leg 1 — Architect confer (CDP)": "fac6bc8f24c71d14f682eb99be102501656752e029e70202ab9525660d178c18",
    "LEAN DISPATCH — {{slug}}/Legs (run in order; one bounded step each)/Leg 3 — Implement ({{implement_model}}, lane {{lane}})": "cc4290fe865e9f2d02666912b6b312184294fe16a35359549bf448e284b4b7f1",
    "LEAN DISPATCH — {{slug}}/Legs (run in order; one bounded step each)/After a failed gate": "a367cb0b33d2fc4bf6e1918b643d7a6db0d259b42504db9295dd13a15f0d1987",
    "LEAN DISPATCH — {{slug}}/Preflight (before any dispatch)": "71fa1056130dfaef22dadcae251df2a67f109558cccba5b7b4c5ed17e3a8a455",
    "LEAN DISPATCH — {{slug}}/Wake registration (every Cursor planner; before any dispatch)/planner=glass:<n>": "c9097fdd6885b86e93bcdec5e54b52af6d6fb9484fb462c6d454c218a9b90035",
    "LEAN DISPATCH — {{slug}}/Selective reading of closeouts (md tooling only)": "caf80ff262ce04a28e9129cac163658a6ad1e1ea06b639c236b9dbebec93c418",
    "LEAN DISPATCH — {{slug}}/Lean context rules": "c2ebb58434cb247dbcac5f5a8db70ea7d85edc0231acbd8c8774d31465bc5318",
    "LEAN DISPATCH — {{slug}}/Completion (quote, do not narrate)": "4e9d58662cc7a4c807d672e771b59d0b921271aee01ab9595e32ffc1ec5d1a06",
    "LEAN DISPATCH — {{slug}}/Light path and fork to heavy": "605da20b5178f87b9285ed6d4ade530c47474d3d0fbd58aebe0ed59af5f7fa00",
    "LEAN DISPATCH — {{slug}}/Packet (externalized)": "e159ea1a307755c1d513b68ba02a95410afe85e07538d6cb9d0c53d97cc014e2",
}

ARCH_CONTEXT_FIELDS = ("arch_context:", "arch_sources:", "floor_tags:", "vision:")
_LEAK_RE = re.compile(r"law_verbatim|^\s*\|[^\n]+\|[^\n]+\|", re.MULTILINE)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _indexed_map(text: str) -> dict[str, object]:
    nav = [s for s in _document_sections(text) if not (s.level == 0 and s.chars == 0)]
    indexed = _assign_section_ids(nav)
    return {row.section_id: row for row in indexed}


def check_sample(path: Path) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    if not any("SAMPLE" in line for line in text.splitlines()[:8]):
        return False, "missing SAMPLE label in packet header"
    missing = [f for f in ARCH_CONTEXT_FIELDS if f not in text]
    if missing:
        return False, f"missing arch_context fields: {missing}"
    for field in ARCH_CONTEXT_FIELDS:
        line = next((ln for ln in text.splitlines() if ln.startswith(field)), "")
        if not line or len(line.strip()) <= len(field):
            return False, f"empty field {field}"
        if "sha256" in field or field == "arch_sources:":
            if "sha256" not in line and "unavailable" not in line and "n/a" not in line:
                return False, f"arch_sources missing hash or unavailable form: {line!r}"
    if _LEAK_RE.search(text):
        return False, "leaked law_verbatim or tag-index table row"
    return True, "PASS"


def check_verdict_fence() -> tuple[bool, str]:
    sample_reply = """\
VERDICT: APPROVE — sample harness

```VISION-ALIGN
verdict: none
pillar: pillar-4
note: fenced inner verdict must not promote
digest_map_sha256: sha256:1b55f27a0147fb3d228cf07bd50bb732d7ad5cd65829dcee0100df7f7d56d1fc · stale: false
```

floor: aligned — sample only
"""
    verdict, _refs = harvest_promoted_verdict_with_coverage(sample_reply)
    if verdict != "VERDICT: APPROVE — sample harness":
        return False, f"expected one promoted verdict, got {verdict!r}"
    return True, "PASS"


def check_protected_sections(path: Path, baseline_tip: str | None) -> tuple[bool, str]:
    if baseline_tip and not sha256_hex_equal(_file_sha256(path), baseline_tip):
        # candidate file won't match live tip; protected check uses own-body hashes only
        pass
    text = path.read_text(encoding="utf-8")
    read_sha = _file_sha256(path)
    idx = _indexed_map(text)
    drift: list[str] = []
    for sid, expected in PROTECTED_SECTION_SHA256.items():
        row = idx.get(sid)
        if row is None:
            drift.append(f"missing section {sid}")
            continue
        body = _section_own_body(text, row)
        if body is None:
            drift.append(f"no body for {sid}")
            continue
        actual = sha256_hex_of_bytes(body.encode("utf-8"))
        if actual != expected:
            drift.append(f"{sid}: {actual} != {expected}")
    if drift:
        return False, "; ".join(drift)
    manifest = build_document_manifest(
        path=str(path),
        sandbox="cortex",
        text=text,
        read_sha256=read_sha,
    )
    live_tip = Path(
        CORTEX_MOUNT / "notes/system/templates/lean-dispatch-ladder.md"
    )
    if live_tip.is_file():
        live_text = live_tip.read_text(encoding="utf-8")
        live_sha = _file_sha256(live_tip)
        live_manifest = build_document_manifest(
            path=str(live_tip),
            sandbox="cortex",
            text=live_text,
            read_sha256=live_sha,
        )
        ids_cand = {s["id"] for s in manifest["sections"]}
        ids_live = {s["id"] for s in live_manifest["sections"]}
        if ids_cand != ids_live:
            return False, f"heading-id drift: +{ids_cand - ids_live} -{ids_live - ids_cand}"
    return True, "PASS"


def check_playbook_section(path: Path) -> tuple[bool, str]:
    text = path.read_text(encoding="utf-8")
    for phrase in ("GAP", "formerly", "previously", "no longer"):
        if phrase in text:
            return False, f"vestigial phrase {phrase!r}"
    if "Vision:" not in text:
        return False, "missing Vision: bullet label"
    if "Web semantics vision" in text:
        return False, "old GAP bullet still present"
    return True, "PASS"


def check_tip_guard() -> tuple[bool, str]:
    live = CORTEX_MOUNT / "notes/system/templates/lean-dispatch-ladder.md"
    if not live.is_file():
        return False, "live template missing"
    tip = _file_sha256(live)
    if not sha256_hex_equal(tip, BASE_TEMPLATE_TIP):
        return False, f"live tip {tip} != expected {BASE_TEMPLATE_TIP} (Leg 4 publish gate)"
    return True, f"PASS live tip still {BASE_TEMPLATE_TIP[:16]}…"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--verdict-fence", action="store_true")
    parser.add_argument("--protected-sections", action="store_true")
    parser.add_argument("--playbook-section", action="store_true")
    parser.add_argument("--tip-guard", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--sample-path", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--candidate-path", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--playbook-path", type=Path, default=DEFAULT_PLAYBOOK_CANDIDATE)
    args = parser.parse_args()

    if not any(
        (
            args.sample,
            args.verdict_fence,
            args.protected_sections,
            args.playbook_section,
            args.tip_guard,
            args.all,
        )
    ):
        parser.error("specify at least one check flag")

    ok = True
    checks: list[tuple[str, bool, str]] = []

    if args.all or args.tip_guard:
        passed, msg = check_tip_guard()
        checks.append(("tip-guard", passed, msg))
        ok &= passed

    if args.all or args.sample:
        passed, msg = check_sample(args.sample_path)
        checks.append(("sample", passed, msg))
        ok &= passed

    if args.all or args.verdict_fence:
        passed, msg = check_verdict_fence()
        checks.append(("verdict-fence", passed, msg))
        ok &= passed

    if args.all or args.protected_sections:
        if not args.candidate_path.is_file():
            checks.append(("protected-sections", False, f"missing {args.candidate_path}"))
            ok = False
        else:
            passed, msg = check_protected_sections(args.candidate_path, None)
            checks.append(("protected-sections", passed, msg))
            ok &= passed

    if args.all or args.playbook_section:
        if not args.playbook_path.is_file():
            checks.append(("playbook-section", False, f"missing {args.playbook_path}"))
            ok = False
        else:
            passed, msg = check_playbook_section(args.playbook_path)
            checks.append(("playbook-section", passed, msg))
            ok &= passed

    for name, passed, msg in checks:
        status = "PASS" if passed else "FAIL"
        print(f"{name}: {status} — {msg}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
