#!/usr/bin/env python3
"""Apply a 1:1 Customize mirror from a container user-zip compare.

Uninstall extras (not Anthropic stock), upload missing catalog slugs, replace
stale bodies. Named slugs only — never ``--all --replace``.

Run on Jupiter (Chrome CDP). Remote seats: ``claude-ai-sync-jupiter recon``.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "libs") not in sys.path:
    sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.container_skills_zip import compare_user_zip  # noqa: E402
from claude_bundles.customize_upload_bytes import build_staged_bytes_map  # noqa: E402
from claude_bundles.resolver import claude_ai_target_slugs  # noqa: E402
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL  # noqa: E402

_UI = _REPO / "scripts" / "cortex" / "upload_claude_bundles_ui.py"
_PY = sys.executable


def _plan(zip_path: Path, repo: Path):
    return compare_user_zip(
        zip_path,
        catalog=set(claude_ai_target_slugs()),
        staged_bytes=build_staged_bytes_map(repo),
    )


def _run_ui(args: list[str], *, dry_run: bool) -> int:
    cmd = [_PY, str(_UI), *args]
    if dry_run:
        print(f"DRY-RUN {' '.join(cmd)}")
        return 0
    print(f"+ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.call(cmd)


def apply_plan(plan, *, cdp_url: str, dry_run: bool) -> int:
    """Uninstall extras → upload missing → replace stale. Return first nonzero."""
    if plan.extra:
        extra_rc = _run_ui(
            [
                "--cdp-url",
                cdp_url,
                "--uninstall",
                "--slugs",
                ",".join(plan.extra),
                "--continue-on-error",
            ],
            dry_run=dry_run,
        )
        if extra_rc:
            return extra_rc
    if plan.missing:
        miss_rc = _run_ui(
            [
                "--cdp-url",
                cdp_url,
                "--slugs",
                ",".join(plan.missing),
                "--continue-on-error",
            ],
            dry_run=dry_run,
        )
        if miss_rc:
            return miss_rc
    if plan.stale:
        stale_rc = _run_ui(
            [
                "--cdp-url",
                cdp_url,
                "--slugs",
                ",".join(plan.stale),
                "--replace",
                "--continue-on-error",
            ],
            dry_run=dry_run,
        )
        if stale_rc:
            return stale_rc
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=_REPO)
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Uninstall extras, upload missing, replace stale",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    plan = _plan(args.zip, args.root)
    payload = {
        **plan.as_dict(),
        "mirrored": plan.mirrored(),
        "apply": bool(args.apply and not args.dry_run),
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(
            f"plan extra={len(plan.extra)} missing={len(plan.missing)} "
            f"stale={len(plan.stale)} mirrored={plan.mirrored()}"
        )
        if plan.extra:
            print("uninstall: " + ",".join(plan.extra))
        if plan.missing:
            print("upload: " + ",".join(plan.missing))
        if plan.stale:
            print("replace: " + ",".join(plan.stale))
    if not args.apply and not args.dry_run:
        return 0 if plan.mirrored() else 1
    rc = apply_plan(plan, cdp_url=args.cdp_url, dry_run=args.dry_run)
    if rc:
        return rc
    if args.dry_run:
        return 0 if plan.mirrored() else 1
    print(
        "applied 1:1 mirror steps; re-run dump-skills + compare to hash-verify"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
