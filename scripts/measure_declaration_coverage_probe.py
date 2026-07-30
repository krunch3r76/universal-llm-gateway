#!/usr/bin/env python3
"""Prospective attribution §7 falsifier 1 probe — declaration coverage measurement.

Given pre/post content-hash snapshots (or admit/closeout hash maps), compute
|G|, |D|, |G\\D|, |D\\G| where:

  G — ground-truth changed paths from snapshot delta (independent of manifest)
  D — declared repo paths from manifest or declaration file

Exits non-zero when ground truth is empty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_dir(root: Path) -> dict[str, str]:
    """Map repo-relative paths to content hashes under *root*."""
    hashes: dict[str, str] = {}
    if not root.is_dir():
        return hashes
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        hashes[rel] = _sha256_file(path)
    return hashes


def ground_truth_paths(pre: dict[str, str], post: dict[str, str]) -> set[str]:
    """Paths whose content hash changed, appeared, or vanished between snapshots."""
    all_paths = set(pre) | set(post)
    changed: set[str] = set()
    for path in all_paths:
        if pre.get(path) != post.get(path):
            changed.add(path)
    return changed


def declared_paths_from_manifest(manifest_path: Path) -> set[str]:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    declared: set[str] = set()
    surfaces = payload.get("surfaces") or {}
    repo = surfaces.get("repo") or {}
    for entry in repo.get("entries") or []:
        op = str(entry.get("op") or "")
        if op not in {"write", "edit", "delete", "observed"}:
            continue
        target = str(entry.get("target") or entry.get("identity") or "").strip()
        if not target or target == ".":
            continue
        declared.add(Path(target).name if "/" not in target else Path(target).as_posix())
    return declared


def declared_paths_from_list(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.strip().lstrip("/") for line in lines if line.strip()}


def load_hash_map(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"hash map must be a JSON object: {path}")
    return {str(k): str(v) for k, v in payload.items()}


def measure(
    *,
    ground_truth: set[str],
    declared: set[str],
) -> dict[str, int | float]:
    g_only = ground_truth - declared
    d_only = declared - ground_truth
    overlap = ground_truth & declared
    g_size = len(ground_truth)
    d_size = len(declared)
    return {
        "|G|": g_size,
        "|D|": d_size,
        "|G∩D|": len(overlap),
        "|G\\D|": len(g_only),
        "|D\\G|": len(d_only),
        "coverage": (len(overlap) / g_size) if g_size else 0.0,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Prospective attribution §7 falsifier 1 probe: compare ground-truth "
            "snapshot delta (G) against declared repo paths (D)."
        )
    )
    parser.add_argument(
        "--worktree",
        type=Path,
        help="Repo worktree root (used with --pre-dir/--post-dir relative layout)",
    )
    parser.add_argument(
        "--pre-dir",
        type=Path,
        help="Directory tree captured immediately before dispatch",
    )
    parser.add_argument(
        "--post-dir",
        type=Path,
        help="Directory tree captured immediately after dispatch",
    )
    parser.add_argument(
        "--pre-hashes",
        type=Path,
        help="JSON object of path→sha256 at admit",
    )
    parser.add_argument(
        "--post-hashes",
        type=Path,
        help="JSON object of path→sha256 at closeout",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        help="EffectsManifest JSON for declared path set D",
    )
    parser.add_argument(
        "--declared-list",
        type=Path,
        help="Newline-separated declared repo-relative paths",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON summary",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.pre_hashes and args.post_hashes:
        pre = load_hash_map(args.pre_hashes)
        post = load_hash_map(args.post_hashes)
        ground_truth = ground_truth_paths(pre, post)
    elif args.pre_dir and args.post_dir:
        ground_truth = ground_truth_paths(
            snapshot_dir(args.pre_dir),
            snapshot_dir(args.post_dir),
        )
    else:
        parser.error("provide (--pre-hashes and --post-hashes) or (--pre-dir and --post-dir)")

    if not ground_truth:
        print("ground truth empty — no observable delta (attribution §7 falsifier 1)", file=sys.stderr)
        return 2

    if args.manifest:
        declared = declared_paths_from_manifest(args.manifest)
    elif args.declared_list:
        declared = declared_paths_from_list(args.declared_list)
    else:
        declared = set()

    stats = measure(ground_truth=ground_truth, declared=declared)
    if args.json:
        print(json.dumps(stats, indent=2, sort_keys=True))
    else:
        for key in ("|G|", "|D|", "|G∩D|", "|G\\D|", "|D\\G|", "coverage"):
            print(f"{key}: {stats[key]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
