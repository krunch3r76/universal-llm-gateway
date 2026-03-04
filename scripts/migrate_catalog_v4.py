#!/usr/bin/env python3
"""
One-shot migration: catalog_schema 3 → 4.

Walks config/models/**/*.yaml, migrates flat capability fields to structured
capabilities block, sets catalog_schema: 4. Skip entries already at v4.

Usage:
    python scripts/migrate_catalog_v4.py [--dry-run] [config/models]
"""

import argparse
import sys
from pathlib import Path

import yaml


def _migrate_entry(entry: dict) -> bool:
    """Migrate single entry in-place. Return True if migrated."""
    if entry.get("catalog_schema") == 4:
        return False

    old_meta = entry.get("metadata", {})
    caps: dict = {}

    # input_schema
    caps["input_schema"] = old_meta.pop("input_schema", "messages")

    # modalities
    is_vision = old_meta.pop("is_vision_model", False)
    vision_arch = old_meta.pop("vision_architecture", None)
    modalities: dict = {
        "input": ["text", "vision"] if is_vision else ["text"],
        "output": ["text"],
    }
    if vision_arch:
        modalities["vision_architecture"] = vision_arch
    caps["modalities"] = modalities

    # interaction
    has_chat = old_meta.pop("has_chat_template", None)
    if has_chat is None:
        has_chat = old_meta.get("supports_chat_history", False)
    caps["interaction"] = {"chat_template": bool(has_chat)}

    # reasoning
    caps["reasoning"] = {"supports_thinking": old_meta.pop("supports_thinking", False)}

    # limits
    training_ctx = old_meta.pop("training_context_length", None)
    if training_ctx:
        caps["limits"] = {"max_context_length": training_ctx}
    else:
        caps["limits"] = {}

    # provenance
    license_val = old_meta.pop("license", None)
    if license_val:
        caps["provenance"] = {"license": license_val}
    else:
        caps["provenance"] = {}

    # cleanup
    old_meta.pop("supports_chat_history", None)
    old_meta.pop("capabilities", None)  # old list[str]
    old_meta.pop("safety_info", None)
    old_meta.pop("tokens_per_image", None)  # stays in loader only

    old_meta["capabilities"] = caps
    entry["catalog_schema"] = 4
    return True


def migrate_file(path: Path, dry_run: bool) -> tuple[bool, str | None]:
    """Migrate one YAML file. Return (migrated, error)."""
    try:
        text = path.read_text()
        data = yaml.safe_load(text)
    except Exception as e:
        return False, str(e)

    if data is None or not isinstance(data, dict):
        return False, "Empty or invalid root"

    if not _migrate_entry(data):
        return False, None  # skipped

    if dry_run:
        return True, None

    try:
        path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))
    except Exception as e:
        return False, str(e)

    return True, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate catalog YAML to schema v4")
    parser.add_argument("--dry-run", action="store_true", help="Do not write files")
    parser.add_argument(
        "root",
        nargs="?",
        default="config/models",
        help="Root directory to scan (default: config/models)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.exists():
        print(f"Error: {root} does not exist", file=sys.stderr)
        return 1

    migrated = 0
    skipped = 0
    errors: list[tuple[Path, str]] = []

    for path in sorted(root.rglob("*.yaml")):
        did_migrate, err = migrate_file(path, args.dry_run)
        if err:
            errors.append((path, err))
        elif did_migrate:
            migrated += 1
            print(f"  Migrated: {path}")
        else:
            skipped += 1

    print(f"\nMigrated: {migrated}, Skipped: {skipped}, Errors: {len(errors)}")
    if errors:
        for p, e in errors:
            print(f"  ERROR {p}: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
