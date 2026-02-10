"""Lint command implementation."""

import argparse

from ...config import Config
from .._shared import load_catalog_yaml
from .rules import lint_catalog


def cmd_lint(args: argparse.Namespace, config: Config) -> int:
    """
    Lint catalog for V2 schema compliance.

    Checks:
        - schema_version = 2
        - All models have schema field
        - No V1 keys (configurations, base_loader)
        - Schema/format compatibility
        - Valid device names (gpu, cpu, hybrid)
        - Device support per schema

    Exit codes:
        0 = passed (warnings OK)
        1 = errors found (must fix)
    """
    catalog, catalog_path = load_catalog_yaml(args, config)
    if not catalog or not catalog_path:
        return 1

    print(f"Linting: {catalog_path}")

    issues = lint_catalog(catalog)

    if not issues:
        models = catalog.get("models", {})
        print(f"✅ Lint passed: {len(models)} models, all V2 compliant")
        return 0

    errors = [i for i in issues if i["severity"] == "error"]
    warnings = [i for i in issues if i["severity"] == "warning"]

    print(f"\n{'=' * 60}")
    print(f"Lint Results: {len(errors)} errors, {len(warnings)} warnings")
    print(f"{'=' * 60}\n")

    if errors:
        print("ERRORS (must fix):\n")
        for issue in errors:
            print(f"  ❌ [{issue['model_id']}] {issue['message']}")
            if issue.get("fix"):
                print(f"     Fix: {issue['fix']}")
        print()

    if warnings:
        print("WARNINGS:\n")
        for issue in warnings:
            print(f"  ⚠️  [{issue['model_id']}] {issue['message']}")
            if issue.get("fix"):
                print(f"     Fix: {issue['fix']}")
        print()

    return 1 if errors else 0
