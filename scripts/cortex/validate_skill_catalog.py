#!/usr/bin/env python3
"""Validate ``config/skills.yaml`` catalog (+ SOT coverage)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.catalog import load_skill_catalog  # noqa: E402
from implement_admission.skill_catalog_resolver import (  # noqa: E402
    catalog_digest,
    catalog_source_uris,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate catalog (default)"
    )
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    root = (args.root or _REPO).resolve()
    catalog = load_skill_catalog(repo_root=root, validate_sot=True)
    uris = catalog_source_uris(repo_root=root)
    if len(uris) != len(catalog.entries):
        print("DRIFT: catalog URI map size mismatch", file=sys.stderr)
        return 1
    _ = args.check
    print(f"OK skill catalog ({len(uris)} slugs, digest={catalog_digest()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
