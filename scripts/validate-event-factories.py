#!/usr/bin/env python3
"""
Validate that all events use factory functions instead of raw Event() construction.

Usage:
    python scripts/validate-event-factories.py              # Validate all Python files
    python scripts/validate-event-factories.py --staged     # Only staged files

Validates:
    - No raw Event(signal=...) usage outside of factory function files
    - Factory functions must be in dedicated events.py files
    - All event emissions must use factory functions

Note: This is development tooling for enforcing architectural patterns.
      Factory functions provide type safety and consistency.
"""

import ast
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    """A violation of the factory function pattern."""

    file_path: Path
    line_number: int
    line_content: str
    reason: str


# Files that are allowed to contain Event(signal=...) - these are factory function definitions
ALLOWED_FACTORY_FILES = {
    "events.py",  # Standard factory function location
    "types.py",  # Gateway event types
    "signals.py",  # IPC signals
}

# Directories to scan
SCAN_DIRS = ["services", "libs"]


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
            # Check if in scan directories
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


def is_factory_function_file(file_path: Path) -> bool:
    """Check if file is a known factory function definition file."""
    return file_path.name in ALLOWED_FACTORY_FILES


def is_event_class_file(file_path: Path) -> bool:
    """Check if file defines the Event class itself."""
    # The Event class is defined in universal_event_bus/events/event.py
    return (
        "universal_event_bus" in str(file_path)
        and file_path.name == "event.py"
        and "events" in str(file_path)
    )


def is_factory_decorator_file(file_path: Path) -> bool:
    """Check if file defines the event_factory decorator itself."""
    # The @event_factory decorator is defined in universal_event_bus/events/factory.py
    return (
        "universal_event_bus" in str(file_path)
        and file_path.name == "factory.py"
        and "events" in str(file_path)
    )


def check_file(file_path: Path) -> list[Violation]:
    """
    Check a Python file for raw Event(signal=...) usage.

    Returns list of violations found.
    """
    violations = []

    # Skip factory function files - they're allowed to use Event(signal=...)
    if is_factory_function_file(file_path):
        return violations

    # Skip Event class definition file - it contains the Event class itself
    if is_event_class_file(file_path):
        return violations

    # Skip factory decorator definition file - it contains decorator examples
    if is_factory_decorator_file(file_path):
        return violations

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as e:
        return [Violation(file_path, 0, "", f"Could not read file: {e}")]

    lines = content.split("\n")

    # Pattern to match Event(signal=... or Event( signal=...
    # This matches:
    # - Event(signal="...", ...)
    # - Event( signal="...", ...)
    # - Event(signal=CONSTANT, ...)
    pattern = re.compile(r"Event\s*\(\s*signal\s*=")

    for line_num, line in enumerate(lines, start=1):
        if pattern.search(line):
            # Check if this is inside a factory function definition
            # Factory functions are functions that return Event
            is_in_factory = False

            try:
                tree = ast.parse(content)
                for node in ast.walk(tree):
                    if isinstance(node, ast.FunctionDef):
                        # Check if function returns Event type
                        # node.returns is the return annotation
                        if node.returns:
                            # Check if it's a Name node with id "Event"
                            if (
                                isinstance(node.returns, ast.Name)
                                and node.returns.id == "Event"
                            ):
                                # Check if the line is within this function's body
                                # Use lineno for start, and try to find end
                                func_start = node.lineno
                                func_end = func_start
                                # Try to get end_lineno if available (Python 3.8+)
                                if hasattr(node, "end_lineno") and node.end_lineno:
                                    func_end = node.end_lineno
                                else:
                                    # Fallback: find last line of function body
                                    if node.body:
                                        last_stmt = node.body[-1]
                                        if (
                                            hasattr(last_stmt, "end_lineno")
                                            and last_stmt.end_lineno
                                        ):
                                            func_end = last_stmt.end_lineno
                                        elif hasattr(last_stmt, "lineno"):
                                            func_end = last_stmt.lineno

                                if func_start <= line_num <= func_end:
                                    is_in_factory = True
                                    break
            except (SyntaxError, AttributeError):
                # If we can't parse, we'll flag it anyway
                pass

            if not is_in_factory:
                violations.append(
                    Violation(
                        file_path=file_path,
                        line_number=line_num,
                        line_content=line.strip(),
                        reason="Raw Event(signal=...) usage detected. Use factory functions from events.py instead.",
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
        print("\n❌ Event Factory Function Violations Found:\n")
        for violation in all_violations:
            rel_path = violation.file_path.relative_to(project_root)
            print(f"  {rel_path}:{violation.line_number}")
            print(f"    {violation.line_content}")
            print(f"    → {violation.reason}\n")

        violation_count = len(all_violations)
        print(
            f"\n❌ Found {violation_count} violation(s). "
            + "Use factory functions from events.py files instead of raw Event() construction."
        )
        print("\nSee: .cursor/rules/architecture/patterns_ws.mdc#event-structure")
        return 1

    print(f"✅ Validated {len(files)} file(s) - all events use factory functions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
