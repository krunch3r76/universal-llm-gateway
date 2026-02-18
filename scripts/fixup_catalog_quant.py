#!/usr/bin/env python3
"""One-shot fixup: correct quant values in static catalog YAML files.

Reads each YAML in config/models/, extracts quant from download.huggingface.file
(or the YAML filename stem), and overwrites the metadata.quant field when it differs.

Usage:
    python scripts/fixup_catalog_quant.py          # dry-run (default)
    python scripts/fixup_catalog_quant.py --apply   # write changes
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = REPO_ROOT / "config" / "models"

sys.path.insert(0, str(REPO_ROOT / "libs"))
from inference_djinn.catalog.extractor.gguf import extract_quant_from_filename  # noqa: E402, I001


def _load_yaml(path: Path) -> dict:
    with path.open() as f:
        return yaml.safe_load(f) or {}


def _save_yaml(path: Path, data: dict) -> None:
    with path.open("w") as f:
        yaml.dump(
            data, f, default_flow_style=False, sort_keys=False, allow_unicode=True
        )


def _derive_quant(entry: dict, yaml_stem: str) -> str | None:
    """Derive quant from download filename, falling back to YAML filename stem."""
    hf_file = entry.get("download", {}).get("huggingface", {}).get("file")
    if hf_file:
        quant = extract_quant_from_filename(hf_file)
        if quant:
            return quant

    return extract_quant_from_filename(yaml_stem)


def fixup_all(apply: bool) -> int:
    """Scan all model YAML files and fix quant values.

    Args:
        apply: If True, write changes to disk. Otherwise dry-run.

    Returns:
        Number of files that need (or received) changes.
    """
    changed = 0
    skipped = 0

    yaml_files = sorted(MODELS_DIR.rglob("*.yaml"))
    if not yaml_files:
        print(f"No YAML files found under {MODELS_DIR}")
        return 0

    for yaml_path in yaml_files:
        entry = _load_yaml(yaml_path)
        metadata = entry.get("metadata", {})
        fmt = metadata.get("format", "")

        if fmt != "gguf":
            continue

        current_quant = metadata.get("quant")
        derived = _derive_quant(entry, yaml_path.stem)

        if derived is None:
            if current_quant is not None:
                rel = yaml_path.relative_to(REPO_ROOT)
                print(f"  SKIP {rel}: cannot derive quant, keeping '{current_quant}'")
                skipped += 1
            continue

        current_str = str(current_quant) if current_quant is not None else None
        if current_str == derived:
            continue

        rel = yaml_path.relative_to(REPO_ROOT)
        print(f"  FIX  {rel}: '{current_str}' → '{derived}'")
        changed += 1

        if apply:
            metadata["quant"] = derived
            entry["metadata"] = metadata
            _save_yaml(yaml_path, entry)

    print()
    mode = "Applied" if apply else "Would change"
    print(f"{mode}: {changed} file(s), skipped: {skipped}")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes (default is dry-run)",
    )
    args = parser.parse_args()

    if not args.apply:
        print("DRY RUN (pass --apply to write changes)\n")

    fixup_all(args.apply)


if __name__ == "__main__":
    main()
