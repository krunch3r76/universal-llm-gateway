#!/usr/bin/env python3
"""
Pre-commit validation for catalog files.

Usage:
    python scripts/hooks/validate_catalog.py              # Validate all
    python scripts/hooks/validate_catalog.py --staged     # Only staged files

Validates:
    - V2 schema compliance (schema field required)
    - Model ID = filename stem
    - Domain structure (ALLOWED_DOMAINS)
    - No V1 patterns (configurations, base_loader)

Note: This is development tooling, not runtime validation.
      Isolated gateways don't run this - CatalogLoader handles runtime validation.
"""

import subprocess
import sys
from pathlib import Path

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "services" / "_universal-llm-gateway" / "src"))

from core.catalog.loading import ALLOWED_DOMAINS  # noqa: E402
from core.catalog.schemas import SchemaRegistry  # noqa: E402


def get_staged_catalog_files() -> list[Path]:
    """Get staged YAML files in catalog directories."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )

    catalog_files = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        path = Path(line)
        if path.suffix == ".yaml" and "config/models/" in str(path):
            catalog_files.append(PROJECT_ROOT / path)

    return catalog_files


def get_all_catalog_files() -> list[Path]:
    """Get all catalog YAML files."""
    models_dir = PROJECT_ROOT / "config" / "models"
    if not models_dir.exists():
        return []
    return list(models_dir.rglob("*.yaml"))


def validate_file(file_path: Path) -> list[str]:
    """Validate single catalog file. Returns list of errors."""
    import yaml

    errors = []

    try:
        with open(file_path) as f:
            entry = yaml.safe_load(f)
    except Exception as e:
        return [f"Invalid YAML: {e}"]

    if not entry:
        return ["Empty file"]

    model_id = file_path.stem

    # Check V2 required fields
    if "schema" not in entry:
        errors.append("Missing 'schema' field (V2 required)")

    if "metadata" not in entry:
        errors.append("Missing 'metadata' field")

    # Check for V1 patterns (fail-fast)
    if "configurations" in entry:
        errors.append("V1 'configurations' key found - use 'devices'")

    if "base_loader" in entry:
        errors.append("V1 'base_loader' key found - use 'loader'")

    # Validate domain structure
    try:
        rel_path = file_path.relative_to(PROJECT_ROOT / "config" / "models")
        domain = rel_path.parts[0]
        if domain not in ALLOWED_DOMAINS:
            errors.append(
                f"Invalid domain '{domain}' - must be one of {sorted(ALLOWED_DOMAINS)}"
            )
    except ValueError:
        pass  # File not under models/

    # Validate schema if present
    if "schema" in entry:
        schema = SchemaRegistry.get_by_engine(entry["schema"])
        if not schema:
            errors.append(f"Unknown schema '{entry['schema']}'")
        else:
            issues = schema.validate(model_id, entry)
            for issue in issues:
                if issue.severity == "error":
                    errors.append(issue.message)

    return errors


def main() -> int:
    staged_only = "--staged" in sys.argv

    if staged_only:
        files = get_staged_catalog_files()
        if not files:
            return 0  # No catalog files staged
    else:
        files = get_all_catalog_files()

    total_errors = 0

    for file_path in files:
        errors = validate_file(file_path)
        if errors:
            rel_path = file_path.relative_to(PROJECT_ROOT)
            print(f"\nX {rel_path}:")
            for err in errors:
                print(f"   - {err}")
            total_errors += len(errors)

    if total_errors > 0:
        print(f"\nX Catalog validation failed: {total_errors} error(s)")
        return 1

    print(f"OK Validated {len(files)} catalog files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
