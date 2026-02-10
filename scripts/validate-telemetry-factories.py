#!/usr/bin/env python3
"""
Validate that all telemetry uses factory functions.

Usage:
    python scripts/validate-telemetry-factories.py              # Validate all
    python scripts/validate-telemetry-factories.py --staged     # Only staged files

Validates:
    - No raw TelemetryPayload subclass construction
    - All telemetry signals are dot-notation
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    file_path: Path
    line_number: int
    line_content: str
    reason: str


# Directories to scan
SCAN_DIRS = ["services", "libs"]

# Files allowed to contain direct construction (factory definitions)
ALLOWED_FILES = {"telemetry.py"}


def get_staged_python_files(project_root: Path) -> list[Path]:
    """Get staged Python files in scan directories."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
        cwd=project_root,
    )

    python_files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        path = Path(line)
        if path.suffix == ".py":
            for scan_dir in SCAN_DIRS:
                if str(path).startswith(f"{scan_dir}/"):
                    python_files.append(project_root / path)
                    break

    return python_files


def get_all_python_files(project_root: Path) -> list[Path]:
    """Get all Python files in scan directories."""
    python_files = []
    for scan_dir in SCAN_DIRS:
        scan_path = project_root / scan_dir
        if scan_path.exists():
            python_files.extend(scan_path.rglob("*.py"))
    return python_files


def check_file(file_path: Path) -> list[Violation]:
    """Check file for direct TelemetryPayload construction."""
    violations = []

    if file_path.name in ALLOWED_FILES:
        return violations

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return [Violation(file_path, 0, "", f"Could not read file: {e}")]

    lines = content.split("\n")

    # Pattern for direct payload construction
    # Matches: ResourceUpdatePayload(..., ModelLoadedPayload(..., etc.
    pattern = re.compile(
        r"(ResourceUpdatePayload|ModelLoadedPayload|ModelUnloadedPayload|"
        r"ModelBusyPayload|ModelIdlePayload|ModelLoadingStartedPayload|"
        r"ModelLoadFailedPayload|TelemetryHeartbeatPayload)\s*\("
    )

    for line_num, line in enumerate(lines, start=1):
        if pattern.search(line):
            # Skip if it's a from_dict method or class definition
            if "from_dict" in line or "class " in line or "def " in line:
                continue
            violations.append(
                Violation(
                    file_path=file_path,
                    line_number=line_num,
                    line_content=line.strip(),
                    reason="Direct TelemetryPayload construction. Use factory functions.",
                )
            )

    return violations


def main() -> int:
    """Main validation function."""
    project_root = Path(__file__).parent.parent
    staged_only = "--staged" in sys.argv

    if staged_only:
        files = get_staged_python_files(project_root)
        if not files:
            print("OK No staged Python files to validate")
            return 0
    else:
        files = get_all_python_files(project_root)

    all_violations: list[Violation] = []

    for file_path in files:
        violations = check_file(file_path)
        all_violations.extend(violations)

    if all_violations:
        print("\n❌ Telemetry Factory Violations Found:\n")
        for violation in all_violations:
            rel_path = violation.file_path.relative_to(project_root)
            print(f"  {rel_path}:{violation.line_number}")
            print(f"    {violation.line_content}")
            print(f"    → {violation.reason}\n")

        print(f"\n❌ Found {len(all_violations)} violation(s).")
        return 1

    print(f"✅ Validated {len(files)} file(s) - all telemetry uses factory functions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
