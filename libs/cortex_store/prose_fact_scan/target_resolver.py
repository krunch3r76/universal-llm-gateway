"""Resolve living-context scan targets with manifest + fail-closed bounds."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from collections.abc import Callable

from .constants import (
    HARD_EXCLUDED_SUBTREES,
    TARGET_COUNT_MAX,
    TARGET_COUNT_MIN,
    TIER_A_GLOBS,
)
from .models import ManifestEntry, ScanTarget, SkippedEntry

OpenFn = Callable[..., object]
_DEFAULT_OPEN = open

DURABLE_IDENTITY_RE = re.compile(
    r"(durable[_ ]identity|decision authority|administrator of|principal)"
    r".*(now|currently|active|suspended|employed|onboarded)|"
    r"(now|currently|active|suspended|employed|onboarded)"
    r".*(durable[_ ]identity|decision authority|administrator of|principal)",
    re.IGNORECASE,
)


def _rel_posix(path: Path, base: Path) -> str:
    return path.relative_to(base).as_posix()


def is_hard_excluded(rel_path: str) -> str | None:
    normalized = rel_path.replace("\\", "/")
    for subtree in HARD_EXCLUDED_SUBTREES:
        marker = f"/{subtree}"
        if normalized.startswith(subtree) or marker in f"/{normalized}/":
            return f"excluded_{subtree.rstrip('/')}"
    return None


def _glob_expand(base: Path, pattern: str) -> list[Path]:
    if "**" in pattern:
        root_part, _, rest = pattern.partition("**")
        root = base / root_part.rstrip("/")
        suffix = rest.lstrip("/") or "*"
        if not root.is_dir():
            return []
        return [p for p in root.rglob(suffix) if p.is_file()]
    return [p for p in base.glob(pattern) if p.is_file()]


def expand_tier_a(base: Path) -> dict[str, str]:
    """Return Tier-A path -> reason map (included_tier_a)."""
    out: dict[str, str] = {}
    for pattern in TIER_A_GLOBS:
        for path in _glob_expand(base, pattern):
            rel = _rel_posix(path, base)
            out[rel] = "included_tier_a"
    return out


def _tier_b_targets(base: Path, tier_a: set[str], read_text: Callable[[Path], str]) -> list[ScanTarget]:
    targets: list[ScanTarget] = []
    for path in base.rglob("*.md"):
        rel = _rel_posix(path, base)
        if rel in tier_a or is_hard_excluded(rel):
            continue
        text = read_text(path)
        lines = text.splitlines()
        start_re = re.compile(r"^\s*<!--\s*handoff:start\s*-->\s*$", re.I)
        end_re = re.compile(r"^\s*<!--\s*handoff:end\s*-->\s*$", re.I)
        in_fence = False
        idx = 0
        while idx < len(lines):
            if lines[idx].strip().startswith("```"):
                in_fence = not in_fence
                idx += 1
                continue
            if in_fence:
                idx += 1
                continue
            if start_re.match(lines[idx]):
                end_idx = idx + 1
                while end_idx < len(lines) and not end_re.match(lines[end_idx]):
                    end_idx += 1
                if end_idx < len(lines):
                    body = "\n".join(lines[idx + 1 : end_idx]).strip()
                    if body:
                        targets.append(
                            ScanTarget(
                                path=rel,
                                reason="included_tier_b_marker",
                                region_start=idx + 2,
                                region_end=end_idx,
                            )
                        )
                        break
            idx += 1
        else:
            if DURABLE_IDENTITY_RE.search(text):
                targets.append(
                    ScanTarget(path=rel, reason="included_tier_b_durable_identity")
                )
    return targets


def resolve_scan_targets(
    base: Path,
    *,
    principal: str | None = None,
    paths: list[str] | None = None,
    unsafe_full_scan: bool = False,
    open_fn: OpenFn | None = None,
) -> dict[str, object]:
    """Build scan targets + manifest. Fail closed outside [80,200] unless override."""
    del principal  # reserved for entity-binder default principal
    opener = open_fn or _DEFAULT_OPEN

    def read_text(path: Path) -> str:
        with opener(path, encoding="utf-8", errors="replace") as handle:  # type: ignore[call-arg]
            return handle.read()

    manifest: list[ManifestEntry] = []
    skipped: list[SkippedEntry] = []
    tier_a_map = expand_tier_a(base)
    tier_a_paths = set(tier_a_map)

    if paths:
        requested = {p.replace("\\", "/").lstrip("/") for p in paths}
        tier_a_paths = tier_a_paths & requested
        for req in requested:
            if req not in tier_a_map:
                ex = is_hard_excluded(req)
                if ex:
                    skipped.append(SkippedEntry(path=req, reason=ex))
                else:
                    skipped.append(SkippedEntry(path=req, reason="excluded_not_in_allowlist"))

    for rel, reason in sorted(tier_a_map.items()):
        if rel in tier_a_paths:
            manifest.append(ManifestEntry(path=rel, reason=reason))
        elif paths:
            ex = is_hard_excluded(rel) or "excluded_not_in_allowlist"
            manifest.append(ManifestEntry(path=rel, reason=ex if ex else "excluded_not_in_allowlist"))

    targets: list[ScanTarget] = [
        ScanTarget(path=entry.path, reason=entry.reason)
        for entry in manifest
        if entry.reason == "included_tier_a"
    ]

    if not paths:
        for tb in _tier_b_targets(base, tier_a_paths, read_text):
            manifest.append(ManifestEntry(path=tb.path, reason=tb.reason))
            targets.append(tb)

    excluded_count = sum(1 for m in manifest if m.reason.startswith("excluded_"))
    target_count = len(targets)
    if not unsafe_full_scan and not (
        TARGET_COUNT_MIN <= target_count <= TARGET_COUNT_MAX
    ):
        return {
            "error": (
                f"Target count {target_count} outside [{TARGET_COUNT_MIN}, "
                f"{TARGET_COUNT_MAX}]; pass unsafe_full_scan=true to override."
            ),
            "target_count": target_count,
            "manifest": [m.__dict__ for m in manifest],
        }

    return {
        "targets": targets,
        "manifest": [m.__dict__ for m in manifest],
        "target_count": target_count,
        "excluded_count": excluded_count,
        "skipped": [s.__dict__ for s in skipped],
    }
