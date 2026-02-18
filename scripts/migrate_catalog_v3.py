"""
One-shot migration: Split V2 catalog files into static + local V3 format.

Static entry (config/models/):
    catalog_schema: 3
    schema, metadata (stripped of activated_*_contexts), download

Local entry (~/.gateway/catalog/):
    catalog_schema: 3
    Full operational entry (all V2 fields intact + catalog_schema key)

Usage:
    python scripts/migrate_catalog_v3.py --dry-run   (default)
    python scripts/migrate_catalog_v3.py --apply
"""

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

# Discover workspace root (script is at scripts/migrate_catalog_v3.py)
WORKSPACE_ROOT = Path(__file__).parent.parent
STATIC_MODELS_DIR = WORKSPACE_ROOT / "config" / "models"
LOCAL_CATALOG_DIR = Path.home() / ".gateway" / "catalog"

METADATA_FIELDS_TO_STRIP = frozenset({"activated_gpu_contexts", "activated_cpu_contexts"})
ENTRY_SECTIONS_TO_STRIP = frozenset({"loader", "devices"})


def _load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Expected dict at root of {path}, got {type(data).__name__}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    """Atomic YAML write via temp file + os.replace."""
    import os
    import tempfile

    class IndentDumper(yaml.SafeDumper):
        def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:  # type: ignore[override]
            return super().increase_indent(flow, False)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(suffix=".yaml.tmp", dir=path.parent, prefix=f".{path.stem}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(
                data,
                f,
                Dumper=IndentDumper,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
                width=120,
            )
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _build_static_entry(v2: dict[str, Any]) -> dict[str, Any]:
    """
    Produce V3 static entry: catalog_schema first, then schema, metadata
    (stripped of activated_*_contexts), download. No loader or devices.
    """
    metadata = {
        k: v
        for k, v in v2.get("metadata", {}).items()
        if k not in METADATA_FIELDS_TO_STRIP
    }
    entry: dict[str, Any] = {"catalog_schema": 3}
    entry["schema"] = v2["schema"]
    entry["metadata"] = metadata
    if "download" in v2:
        entry["download"] = v2["download"]
    return entry


def _build_local_entry(v2: dict[str, Any]) -> dict[str, Any]:
    """
    Produce V3 local entry: catalog_schema first, then all existing V2 fields verbatim.
    """
    entry: dict[str, Any] = {"catalog_schema": 3}
    for k, v in v2.items():
        entry[k] = v
    return entry


def _discover_model_files() -> list[tuple[Path, Path]]:
    """
    Discover all model YAML files under config/models/.

    Returns:
        List of (yaml_path, relative_path_from_models_dir) tuples
    """
    if not STATIC_MODELS_DIR.exists():
        print(f"ERROR: Static models dir not found: {STATIC_MODELS_DIR}", file=sys.stderr)
        sys.exit(1)

    files: list[tuple[Path, Path]] = []
    for yaml_file in sorted(STATIC_MODELS_DIR.rglob("*.yaml")):
        rel = yaml_file.relative_to(STATIC_MODELS_DIR)
        if len(rel.parts) < 3:
            continue  # Skip files not in domain/engine/model.yaml structure
        files.append((yaml_file, rel))
    return files


def migrate(*, apply: bool) -> int:
    """
    Run migration.

    Args:
        apply: If True, write files. If False, dry-run only.

    Returns:
        Exit code (0 = success, 1 = errors)
    """
    mode = "APPLY" if apply else "DRY-RUN"
    print(f"[{mode}] Migrating catalog to V3...")
    print(f"  Static catalog: {STATIC_MODELS_DIR}")
    print(f"  Local catalog:  {LOCAL_CATALOG_DIR}")
    print()

    model_files = _discover_model_files()
    if not model_files:
        print("ERROR: No model files found", file=sys.stderr)
        return 1

    processed = 0
    static_written = 0
    local_written = 0
    errors = 0

    for yaml_path, rel in model_files:
        model_id = yaml_path.stem
        try:
            v2 = _load_yaml(yaml_path)

            # Skip already-migrated files
            if v2.get("catalog_schema") == 3:
                print(f"  SKIP {model_id}: already V3")
                continue

            if "schema" not in v2:
                print(f"  ERROR {model_id}: missing 'schema' field", file=sys.stderr)
                errors += 1
                continue

            static_entry = _build_static_entry(v2)
            local_entry = _build_local_entry(v2)

            static_path = STATIC_MODELS_DIR / rel
            local_path = LOCAL_CATALOG_DIR / rel

            if apply:
                _write_yaml(static_path, static_entry)
                _write_yaml(local_path, local_entry)
                print(f"  OK  {model_id}")
                print(f"      static: {static_path.relative_to(WORKSPACE_ROOT)}")
                print(f"      local:  {local_path}")
            else:
                print(f"  WOULD {model_id}")
                print(f"      static: {static_path.relative_to(WORKSPACE_ROOT)}")
                print(f"      local:  {local_path}")

            processed += 1
            static_written += 1
            local_written += 1

        except Exception as e:
            print(f"  ERROR {model_id}: {e}", file=sys.stderr)
            errors += 1

    print()
    print(f"Results [{mode}]:")
    print(f"  Total files:    {len(model_files)}")
    print(f"  Processed:      {processed}")
    print(f"  Static written: {static_written}")
    print(f"  Local written:  {local_written}")
    print(f"  Errors:         {errors}")

    if not apply:
        print()
        print("Dry-run complete. Run with --apply to write files.")

    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate catalog from V2 to V3 (static/local split)"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Write files (default: dry-run only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview changes without writing (default behavior)",
    )
    args = parser.parse_args()

    # --dry-run is the default; --apply overrides
    apply = args.apply and not args.dry_run
    sys.exit(migrate(apply=apply))


if __name__ == "__main__":
    main()
