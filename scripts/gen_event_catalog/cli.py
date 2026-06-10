"""CLI: generate | check (enforcing) | --staged predicate."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import tomllib
from collections import defaultdict

from .doc_ops import (
    build_overlay,
    iter_regions,
    patch_doc,
    region_signals,
    verify_generated_spans,
    wrap_mcp_tables,
    wrap_standard_tables,
    write_overlay,
)
from .extract import PROJECT_ROOT, WALK_ROOTS, extract_factories, load_exceptions
from .render import render_json_sidecar, render_region

_EXCEPTIONS = PROJECT_ROOT / "scripts" / "gen_event_catalog" / "exceptions.toml"
_OVERLAY = PROJECT_ROOT / "docs" / "event-contracts.overlay.toml"
_DOC = PROJECT_ROOT / "docs" / "event-contracts.md"
_JSON_OUT = PROJECT_ROOT / "docs" / "event-contracts.catalog.json"
_EVENT_FILE_ROOTS = tuple(f"{r}/" for r in WALK_ROOTS)


def _inventory_sha(records) -> str:
    blob = "".join(
        f"{r.signal}:{','.join(r.required_keys)}\n"
        for r in sorted(records, key=lambda r: r.signal)
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


def _load_overlay() -> dict[str, str]:
    if not _OVERLAY.exists():
        return {}
    data = tomllib.loads(_OVERLAY.read_text(encoding="utf-8"))
    return data.get("optional_payload", {})


def _by_domain(records):
    grouped = defaultdict(list)
    for r in records:
        grouped[r.domain].append(r)
    return grouped


def _staged_paths() -> set[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return {ln.strip() for ln in out.stdout.splitlines() if ln.strip()}


def _is_event_source(path: str) -> bool:
    if not path.endswith(".py") or not path.startswith(_EVENT_FILE_ROOTS):
        return False
    parts = path.split("/")
    return "events" in parts or parts[-1].startswith("events")


def _is_relevant(staged: set[str]) -> bool:
    return any(
        p.startswith("docs/event-contracts") or _is_event_source(p) for p in staged
    )


def _rendered_region(
    region: str, records: list, overlay: dict[str, str], sha: str
) -> str:
    recs = _by_domain(records).get(region, [])
    block = render_region(region, recs, overlay, sha)
    lines = block.splitlines()
    if lines and "GENERATED:START" in lines[0]:
        lines = lines[1:]
    if lines and "GENERATED:END" in lines[-1]:
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _check_doc(records, overlay: dict[str, str], sha: str) -> int:
    if not _DOC.exists():
        print("❌ docs/event-contracts.md missing", file=sys.stderr)
        return 1

    dynamic = [r for r in records if r.signal_dynamic]
    if dynamic:
        print(f"❌ {len(dynamic)} dynamic-unresolved factories remain")
        for r in dynamic[:10]:
            print(f"  dynamic: {r.factory_name} ({r.source_path}:{r.lineno})")
        return 1

    text = _DOC.read_text(encoding="utf-8")
    span_errors = verify_generated_spans(text)
    if span_errors:
        print("❌ GENERATED span integrity failures:")
        for e in span_errors[:20]:
            print(f"  {e}")
        return 1

    regions = list(iter_regions(text))
    if not regions:
        print("❌ no GENERATED regions in docs/event-contracts.md", file=sys.stderr)
        return 1

    factory_by_domain = _by_domain(records)
    errors: list[str] = []
    for region, _s, _e, inner in regions:
        expected = _rendered_region(region, records, overlay, sha)
        actual = inner.strip()
        if expected != actual:
            errors.append(f"region={region}: content drift (re-run generate)")
        doc_sigs = region_signals(inner)
        factory_sigs = {r.signal for r in factory_by_domain.get(region, [])}
        missing = factory_sigs - doc_sigs
        extra = doc_sigs - factory_sigs
        if missing:
            errors.append(f"region={region}: missing signals: {sorted(missing)[:5]}")
        if extra:
            errors.append(f"region={region}: stale signals: {sorted(extra)[:5]}")

    if errors:
        print("❌ event-contracts catalog out of sync")
        for e in errors[:30]:
            print(f"  {e}")
        return 1

    print(
        f"✅ event-contracts catalog in sync ({len(records)} factories, {len(regions)} regions)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate / check the event-contracts catalog"
    )
    ap.add_argument(
        "command", choices=["generate", "check", "migrate-overlay", "wrap-markers"]
    )
    ap.add_argument("--staged", action="store_true")
    ap.add_argument(
        "--write-overlay",
        action="store_true",
        help="Write overlay from doc col-3 scrape",
    )
    args = ap.parse_args(argv)

    if args.staged and not _is_relevant(_staged_paths()):
        print("OK No staged event-contract changes")
        return 0

    records = extract_factories(WALK_ROOTS, exceptions=load_exceptions(_EXCEPTIONS))
    overlay = _load_overlay()
    sha = _inventory_sha(records)
    grouped = _by_domain(records)

    if args.command == "migrate-overlay":
        text = _DOC.read_text(encoding="utf-8")
        scraped = build_overlay(text)
        write_overlay(_OVERLAY, scraped)
        print(
            f"Wrote {len(scraped)} optional_payload entries to {_OVERLAY.relative_to(PROJECT_ROOT)}"
        )
        return 0

    if args.command == "wrap-markers":
        text = _DOC.read_text(encoding="utf-8")
        text = wrap_standard_tables(text, sha)
        text = wrap_mcp_tables(text, sha)
        _DOC.write_text(text, encoding="utf-8")
        print("Wrapped catalog tables with GENERATED markers")
        return 0

    if args.command == "generate":
        _JSON_OUT.write_text(render_json_sidecar(records), encoding="utf-8")

        if args.write_overlay and _DOC.exists():
            write_overlay(_OVERLAY, build_overlay(_DOC.read_text(encoding="utf-8")))

        if _DOC.exists() and "<!-- GENERATED:START" in _DOC.read_text(encoding="utf-8"):
            patch_doc(_DOC, records, overlay, sha)
            print(
                f"Regenerated GENERATED regions in {_DOC.relative_to(PROJECT_ROOT)}",
                file=sys.stderr,
            )
        else:
            for domain, recs in sorted(grouped.items()):
                print(render_region(domain, recs, overlay, sha))
                print()
        print(
            f"# {len(records)} factories, {len(grouped)} domains",
            file=sys.stderr,
        )
        return 0

    return _check_doc(records, overlay, sha)


if __name__ == "__main__":
    sys.exit(main())
