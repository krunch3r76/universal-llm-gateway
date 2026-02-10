#!/usr/bin/env python3
"""
Detect Event() constructor violations (should use @event_factory functions).

This script finds all direct Event() constructions that are NOT inside
@event_factory decorated functions, which violates the event factory pattern.
"""

import ast
import sys
from pathlib import Path


class EventConstructorVisitor(ast.NodeVisitor):
    """Find Event() calls outside of @event_factory decorated functions."""

    def __init__(self, filepath: Path):
        self.filepath = filepath
        self.violations = []
        self.in_factory = False

    def visit_FunctionDef(self, node):
        # Check if function has @event_factory decorator
        has_factory_decorator = any(
            (isinstance(dec, ast.Name) and dec.id == "event_factory")
            or (isinstance(dec, ast.Attribute) and dec.attr == "event_factory")
            for dec in node.decorator_list
        )

        old_in_factory = self.in_factory
        if has_factory_decorator:
            self.in_factory = True

        self.generic_visit(node)

        self.in_factory = old_in_factory

    def visit_Call(self, node):
        # Check if this is Event(...) call
        is_event_call = False

        if isinstance(node.func, ast.Name) and node.func.id == "Event":
            is_event_call = True
        elif isinstance(node.func, ast.Attribute) and node.func.attr == "Event":
            is_event_call = True

        # Exclude asyncio.Event, ReloadEvent, StargateEvent, etc.
        if is_event_call and isinstance(node.func, ast.Name):
            # Only flag universal_event_bus.Event
            if not self.in_factory:
                self.violations.append(
                    {
                        "file": str(self.filepath),
                        "line": node.lineno,
                        "col": node.col_offset,
                    }
                )

        self.generic_visit(node)


def check_file(filepath: Path) -> list[dict]:
    """Check a single Python file for Event() violations."""
    try:
        with open(filepath) as f:
            content = f.read()

        # Skip files that don't import Event
        imports_event = (
            "from universal_event_bus import" in content
            or "from src.core.events import" in content
        )
        if not imports_event or "Event" not in content:
            return []

        tree = ast.parse(content, filename=str(filepath))
        visitor = EventConstructorVisitor(filepath)
        visitor.visit(tree)
        return visitor.violations
    except Exception as e:
        print(f"⚠️  Error parsing {filepath}: {e}", file=sys.stderr)
        return []


def main():
    """Scan codebase for Event() violations."""
    root = Path(__file__).parent.parent
    search_paths = [
        root / "services" / "universal-stargate",
        root / "services" / "_universal-llm-gateway" / "src",
    ]

    all_violations = []

    for search_path in search_paths:
        if not search_path.exists():
            continue

        for pyfile in search_path.rglob("*.py"):
            violations = check_file(pyfile)
            all_violations.extend(violations)

    if all_violations:
        print(f"❌ Found {len(all_violations)} Event() violations:\n")
        for v in all_violations:
            rel_path = Path(v["file"]).relative_to(root)
            print(f"  {rel_path}:{v['line']}:{v['col']}")
        print(
            "\n💡 These should use @event_factory functions instead of direct Event() construction."
        )
        return 1
    else:
        print("✅ No Event() violations found!")
        return 0


if __name__ == "__main__":
    sys.exit(main())
