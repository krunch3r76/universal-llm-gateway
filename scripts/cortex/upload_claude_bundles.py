#!/usr/bin/env python3
"""Export or upload ``.claude/skills/`` bundles for claude.ai parity.

Two stores — do not conflate:

- **claude.ai Customize → Skills** (your 51-skill UI list): upload zips via
  ``--write-zips``. Operator uploads each zip at https://claude.ai/customize/skills
- **Anthropic Skills API** (``POST /v1/skills``): Messages API / code-execution
  containers only. Does **not** populate Customize. Use ``--api`` explicitly.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

import httpx  # noqa: E402

from claude_bundles.resolver import CLAUDE_BUNDLE_SLUGS  # noqa: E402
from claude_bundles.skills_api import (  # noqa: E402
    _API_BASE,
    build_slug_index,
    create_skill,
    create_skill_version,
    default_headers,
    delete_skill,
    latest_version_name,
    list_custom_skills,
    load_api_key,
    md_zip_entry_name,
    multipart_files,
    validate_bundle_dir,
    write_skill_zip,
    write_md_zip,
)

# Thread 4049 turn 4 — skills indexed locally but not yet on claude.ai.
GAP_SLUGS: tuple[str, ...] = (
    "add-mcp-tool",
    "build-pipeline",
    "corpus-cross-reference-discipline",
    "corpus-grounded-skill-authoring",
    "corpus-map-authoring",
    "cursor-rule-authoring",
    "debug-with-events",
    "document-lifecycle-tracking",
    "docx-ingestion",
    "email-tool-dispatch",
    "enrichment-quality-discipline",
    "entity-creation-discipline",
    "entity-lifecycle-discipline",
    "evidence-review-discipline",
    "friction-review",
    "fs",
    "git-posture",
    "handoff-packet-authoring",
    "handoff-pickup",
    "handoff-prompt-authoring",
    "image-video-generation",
    "implement-todo",
    "implement-work-item",
    "implementation-plan-workflow",
    "lead-seat-boot",
    "mcp-surface-change",
    "mcp-tool-loop-trace-matrix",
    "orchestrator-workflow",
    "pipeline-substrate-capabilities",
    "pre-deploy-gate-discipline",
    "produce-uml",
    "provenance-granularity",
    "refine-pipeline",
    "required-skills-pickup",
    "research-article-ingest",
    "service-lifecycle",
    "session-close-audit",
    "session-close-kernel",
    "ulg-architecture",
)

PILOT_SLUGS: tuple[str, ...] = ("fs", "session-close-kernel", "add-mcp-tool")

# Orphan API creates from pilot (Customize UI path confirmed wrong store).
PILOT_API_SKILL_IDS: tuple[str, ...] = (
    "skill_019S9ZdYqetbrdLHKz7c6TEJ",
    "skill_014KaU7btXhmK7PUvtjgE2Ec",
    "skill_01KD13y9A6toGsqEk7niG8B9",
)


def _workspace_root() -> Path:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return Path(out)
    except subprocess.CalledProcessError:
        return _REPO


def _bundle_dir(root: Path, slug: str) -> Path:
    return root / ".claude" / "skills" / slug


def _parse_slugs(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [s.strip() for s in raw.split(",") if s.strip()]


def _resolve_targets(args: argparse.Namespace) -> list[str]:
    if args.slugs:
        return _parse_slugs(args.slugs)
    if args.pilot:
        return list(PILOT_SLUGS)
    if args.gap:
        return list(GAP_SLUGS)
    return list(CLAUDE_BUNDLE_SLUGS)


def _validate_targets(root: Path, targets: list[str], *, skip_invalid: bool = False) -> list[str]:
    valid: list[str] = []
    errors = 0
    for slug in targets:
        try:
            validate_bundle_dir(slug, _bundle_dir(root, slug))
            valid.append(slug)
        except ValueError as exc:
            if skip_invalid:
                print(f"SKIP: {exc}", file=sys.stderr)
            else:
                print(f"ERROR: {exc}", file=sys.stderr)
                errors += 1
    if errors:
        return []
    return valid


def _write_md_zip(root: Path, targets: list[str], out_path: Path, *, name_pattern: str) -> int:
    skills_root = root / ".claude" / "skills"
    path = write_md_zip(targets, skills_root, out_path, name_pattern=name_pattern)
    import zipfile

    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    print(f"MD-ZIP {path} ({path.stat().st_size} bytes, {len(names)} files)")
    for slug, name in zip(targets, names):
        print(f"  {slug} → {name}")
    return 0


def _write_zips(root: Path, targets: list[str], out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    for slug in targets:
        path = write_skill_zip(slug, _bundle_dir(root, slug), out_dir / f"{slug}.zip")
        print(f"ZIP {slug}: {path} ({path.stat().st_size} bytes)")
    print(f"OK wrote {len(targets)} zip(s) → {out_dir}")
    print("Upload each at https://claude.ai/customize/skills (+ → upload zip)")
    return 0


def _upload_one(
    client: httpx.Client,
    slug_index: dict[str, str],
    slug: str,
    bundle_dir: Path,
    *,
    dry_run: bool,
    force_create: bool,
) -> str:
    name, description = validate_bundle_dir(slug, bundle_dir)
    files = multipart_files(slug, bundle_dir)
    existing_id = slug_index.get(name)
    action = "version" if existing_id and not force_create else "create"
    if dry_run:
        print(
            f"DRY-RUN {slug}: would {action}"
            f"{f' skill_id={existing_id}' if existing_id else ''}"
            f" desc_len={len(description)} files={len(files)}"
        )
        return action

    if existing_id and not force_create:
        result = create_skill_version(client, existing_id, files)
        version = result.get("version", "?")
        print(f"VERSION {slug}: skill_id={existing_id} version={version}")
        return "version"

    result = create_skill(client, files)
    skill_id = result.get("id", "?")
    print(f"CREATE {slug}: skill_id={skill_id}")
    slug_index[name] = str(skill_id)
    return "create"


def _delete_api_pilot(client: httpx.Client) -> int:
    errors = 0
    for skill_id in PILOT_API_SKILL_IDS:
        try:
            delete_skill(client, skill_id)
            print(f"DELETE {skill_id}")
        except httpx.HTTPStatusError as exc:
            print(f"ERROR {skill_id}: HTTP {exc.response.status_code}", file=sys.stderr)
            errors += 1
    remaining = list_custom_skills(client)
    print(f"Remaining custom API skills: {len(remaining)}")
    for row in remaining:
        name = latest_version_name(client, row["id"])
        print(f"  {name}: {row['id']}")
    return errors


def _run_api_upload(args: argparse.Namespace, root: Path, targets: list[str]) -> int:
    if args.dry_run:
        for slug in targets:
            name, description = validate_bundle_dir(slug, _bundle_dir(root, slug))
            nfiles = len(multipart_files(slug, _bundle_dir(root, slug)))
            print(f"DRY-RUN {slug}: ok name={name} desc_len={len(description)} files={nfiles}")
        print(f"OK dry-run ({len(targets)} skills)")
        print("NOTE: --api uploads do NOT appear in claude.ai Customize → Skills")
        return 0

    api_key = load_api_key()
    if not api_key:
        print("ERROR: ANTHROPIC_API_KEY not set and ~/.gateway/secrets.env missing", file=sys.stderr)
        return 1

    stats = {"create": 0, "version": 0}
    errors = 0
    with httpx.Client(base_url=_API_BASE, headers=default_headers(api_key), timeout=120.0) as client:
        if args.delete_api_pilot:
            errors += _delete_api_pilot(client)
            if not targets:
                return 1 if errors else 0

        slug_index = build_slug_index(client)
        print(f"Indexed {len(slug_index)} existing custom skills by name")
        for i, slug in enumerate(targets):
            if i and args.sleep:
                time.sleep(args.sleep)
            try:
                action = _upload_one(
                    client,
                    slug_index,
                    slug,
                    _bundle_dir(root, slug),
                    dry_run=False,
                    force_create=args.force_create,
                )
                stats[action] += 1
            except httpx.HTTPStatusError as exc:
                body = exc.response.text[:500]
                print(f"ERROR {slug}: HTTP {exc.response.status_code} {body}", file=sys.stderr)
                errors += 1
            except ValueError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
                errors += 1

    print(f"Done: create={stats['create']} version={stats['version']} errors={errors}")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--write-zips",
        metavar="DIR",
        help="One zip per skill: {slug}/SKILL.md inside each zip",
    )
    parser.add_argument(
        "--write-md-zip",
        metavar="FILE",
        help="EXPERIMENTAL flat .md zip — claude.ai UI rejects this; use --write-zips",
    )
    parser.add_argument(
        "--md-name-pattern",
        default="agent-skill-{slug}.md",
        help="Zip entry template; must include {slug} (default: agent-skill-{slug}.md)",
    )
    parser.add_argument(
        "--api",
        action="store_true",
        help="Upload via Skills API (Messages API only — NOT claude.ai Customize)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate only; no writes")
    parser.add_argument("--pilot", action="store_true", help=f"Target slugs {PILOT_SLUGS}")
    parser.add_argument("--gap", action="store_true", help="Target 39-skill gap list")
    parser.add_argument("--slugs", help="Comma-separated slug list")
    parser.add_argument("--regen", action="store_true", help="Run gen_claude_bundles.py first")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds between API uploads")
    parser.add_argument("--force-create", action="store_true", help="API: always POST /v1/skills")
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Skip slugs failing validation (e.g. description >200 for claude.ai UI)",
    )
    parser.add_argument(
        "--delete-api-pilot",
        action="store_true",
        help="Delete the 3 orphan Skills API creates from the pilot",
    )
    args = parser.parse_args()

    if not args.write_zips and not args.write_md_zip and not args.api and not args.delete_api_pilot:
        parser.error("Specify --write-md-zip FILE, --write-zips DIR, or --api")

    root = _workspace_root()
    targets = _resolve_targets(args) if not args.delete_api_pilot or args.gap or args.pilot or args.slugs else []

    if args.regen:
        subprocess.check_call(
            [sys.executable, str(_REPO / "scripts/cortex/gen_claude_bundles.py")],
            cwd=root,
        )

    if targets:
        validated = _validate_targets(root, targets, skip_invalid=args.skip_invalid)
        if not validated:
            return 1
        targets = validated

    if args.write_md_zip:
        if "{slug}" not in args.md_name_pattern:
            parser.error("--md-name-pattern must contain {slug}")
        if args.dry_run:
            for slug in targets:
                name, description = validate_bundle_dir(slug, _bundle_dir(root, slug))
                entry = md_zip_entry_name(slug, pattern=args.md_name_pattern)
                print(f"DRY-RUN {slug} → {entry} (desc_len={len(description)})")
            print(f"OK dry-run ({len(targets)} md files → {args.write_md_zip})")
            return 0
        return _write_md_zip(
            root, targets, Path(args.write_md_zip), name_pattern=args.md_name_pattern
        )

    if args.write_zips:
        if args.dry_run:
            for slug in targets:
                name, description = validate_bundle_dir(slug, _bundle_dir(root, slug))
                print(f"DRY-RUN {slug}: ok name={name} desc_len={len(description)}")
            print(f"OK dry-run ({len(targets)} skills)")
            return 0
        return _write_zips(root, targets, Path(args.write_zips))

    return _run_api_upload(args, root, targets)


if __name__ == "__main__":
    raise SystemExit(main())
