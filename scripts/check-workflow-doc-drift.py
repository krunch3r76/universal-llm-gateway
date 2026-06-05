#!/usr/bin/env -S python3.12
"""Drift gate: workflow docs must cite real pipeline_ids and cortex tool ops.

Scans .cursor/commands, .cursor/rules (workspace + shared parent), and
agent-guides for citations that would mislead agents (phantom pipelines/ops).

Exit 0 if clean, 1 if drift. Run via scripts/agent-surface-check.

Negative self-test: --expect-violation task-seed (proves detector fires).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROJECTS_ROOT = REPO_ROOT.parent
SHARED_RULES = PROJECTS_ROOT / ".cursor" / "rules"
SHARED_COMMANDS = PROJECTS_ROOT / ".cursor" / "commands"
PIPELINES_ROOT = REPO_ROOT / "pipelines"
LIBS_ROOT = REPO_ROOT / "libs"

# Retired subsystem — gate fails if these appear outside explicit RETIRED markers.
# Documented or stub ops not yet in public _OPS (see ops_assertions_write).
PENDING_CORTEX_OPS = frozenset({"friction_close"})

RETIRED_CITATIONS = frozenset(
    {
        "task-seed",
        "task_seed",
        "task-close",
        "task_close",
        "task_candidates",
        "boot-tasks",
        "boot_tasks",
    }
)

PIPELINE_ID_RE = re.compile(
    r'pipeline(?:_id)?\s*[=:]\s*["\']([a-z][a-z0-9_-]*)["\']',
    re.IGNORECASE,
)
PIPELINE_COLON_RE = re.compile(r"pipeline:([a-z][a-z0-9_-]*)")
CORTEX_TOOL_RE = re.compile(
    r'cortex\s*\(\s*tool\s*=\s*["\']([a-z_]+)["\']',
    re.IGNORECASE,
)
CORTEX_TOOL_JSON_RE = re.compile(
    r'cortex\(tool="([a-z_]+)"',
    re.IGNORECASE,
)

SCAN_SUFFIXES = {".md", ".mdc"}


def _load_cortex_ops() -> set[str]:
    sys.path.insert(0, str(LIBS_ROOT))
    from cortex_store.dispatch_ops import _OPS  # noqa: PLC0415

    return set(_OPS.keys())


def _load_pipeline_ids(pipelines_root: Path) -> set[str]:
    ids: set[str] = set()
    if not pipelines_root.is_dir():
        return ids
    for yaml_path in pipelines_root.rglob("*.yaml"):
        if "FROZEN" in yaml_path.name or yaml_path.name.startswith("."):
            continue
        try:
            content = yaml_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in content.splitlines():
            if line.startswith("id:"):
                pid = line.split(":", 1)[1].strip().strip('"').strip("'")
                if pid and not pid.startswith("#"):
                    ids.add(pid)
                break
    return ids


def _scan_roots() -> list[Path]:
    roots = [
        REPO_ROOT / ".cursor" / "commands",
        REPO_ROOT / ".cursor" / "rules",
        REPO_ROOT / "agent-guides",
        REPO_ROOT / "docs" / "agent-guides",
        SHARED_COMMANDS,
        SHARED_RULES,
    ]
    return [p for p in roots if p.is_dir()]


def _line_has_retired_excuse(line: str) -> bool:
    lower = line.lower()
    return "retired" in lower or "scrubbed" in lower or "phantom" in lower


def _scan_file(
    path: Path,
    pipeline_ids: set[str],
    cortex_ops: set[str],
    *,
    allow_retired: bool,
) -> list[str]:
    findings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [f"{path}: unreadable ({exc})"]

    for lineno, line in enumerate(text.splitlines(), start=1):
        if _line_has_retired_excuse(line):
            continue

        for pid in PIPELINE_ID_RE.findall(line) + PIPELINE_COLON_RE.findall(line):
            if pid in RETIRED_CITATIONS and not allow_retired:
                findings.append(
                    f"{path}:{lineno}: retired pipeline citation `{pid}`"
                )
            elif pid not in pipeline_ids and pid not in RETIRED_CITATIONS:
                findings.append(
                    f"{path}:{lineno}: unknown pipeline_id `{pid}`"
                )

        for op in CORTEX_TOOL_RE.findall(line) + CORTEX_TOOL_JSON_RE.findall(line):
            if op in RETIRED_CITATIONS and not allow_retired:
                findings.append(
                    f"{path}:{lineno}: retired cortex op `{op}`"
                )
            elif op not in cortex_ops and op not in PENDING_CORTEX_OPS:
                findings.append(f"{path}:{lineno}: unknown cortex op `{op}`")

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--expect-violation",
        metavar="TOKEN",
        help="Self-test: assert at least one finding mentions TOKEN",
    )
    args = parser.parse_args(argv)

    cortex_ops = _load_cortex_ops()
    pipeline_ids = _load_pipeline_ids(PIPELINES_ROOT)

    all_findings: list[str] = []
    fixture = REPO_ROOT / "scripts/fixtures/workflow-doc-drift-negative-fixture.md"
    for root in _scan_roots():
        for path in sorted(root.rglob("*")):
            if path.suffix not in SCAN_SUFFIXES or not path.is_file():
                continue
            if path.resolve() == fixture.resolve():
                continue
            all_findings.extend(
                _scan_file(path, pipeline_ids, cortex_ops, allow_retired=False)
            )

    if args.expect_violation:
        fixture = REPO_ROOT / "scripts/fixtures/workflow-doc-drift-negative-fixture.md"
        fixture_findings = _scan_file(
            fixture, pipeline_ids, cortex_ops, allow_retired=False
        )
        joined = "\n".join(fixture_findings)
        if args.expect_violation not in joined:
            print(
                f"ERROR: expected violation containing {args.expect_violation!r} "
                f"in {fixture}, got: {fixture_findings!r}",
                file=sys.stderr,
            )
            return 2
        print(f"OK expect-violation: detected {args.expect_violation!r}")
        return 0

    if all_findings:
        print("workflow-doc-drift: FAIL\n" + "\n".join(all_findings), file=sys.stderr)
        return 1

    print(
        f"OK check-workflow-doc-drift: {len(pipeline_ids)} pipelines, "
        f"{len(cortex_ops)} cortex ops, scanned {len(_scan_roots())} roots"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
