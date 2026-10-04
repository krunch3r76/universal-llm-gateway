"""G7 land closeout ``restart_owed:`` from the landed diff.

Slugs come from ``slug_for_service_path`` and ``_SERVICE_OWNERSHIP``
path prefixes. A ``libs/`` path uses ``serving_services_for_lib_path``
(the job set), not may-import ownership. Docs and other non-serving
trees add no slug. A services or libs path no rule maps is reported
as ``unmapped:`` rather than dropped.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from implement_admission.propagation_row import is_lib_test_module
from implement_admission.service_lib_ownership import (
    lib_name_for_path,
    service_ownership,
    serving_services_for_lib_path,
    slug_for_service_path,
    unserved_libs,
)

_GIT_TIMEOUT_S = 10.0
_SHA_FILE_RE = re.compile(r"[0-9a-f]{4,64}")

_SKIP_PREFIXES = (
    "docs/",
    "cursor-plugins/",
    ".cursor/",
    ".claude/",
    "tasks/",
    "tmp/",
)


def _norm(path: str) -> str:
    text = path.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _slug_for_prefix(path: str) -> str | None:
    """Any file under a declared service prefix, including non-Python."""
    for slug, own in service_ownership().items():
        if path.startswith(own.path_prefix):
            return slug
    return None


def _skipped_non_serving(path: str) -> bool:
    if path.startswith(_SKIP_PREFIXES):
        return True
    if (
        path.endswith(".md")
        and not path.startswith("services/")
        and not path.startswith("libs/")
    ):
        return True
    return False


def restart_owed_line(paths: Sequence[str]) -> str:
    """Return the closeout block for *paths*.

    ``restart_owed: none`` when no manage slug is owed. Slugs are
    comma-separated and sorted. Unmapped paths follow on their own lines.
    """
    slugs: set[str] = set()
    unmapped: set[str] = set()
    for raw in paths:
        path = _norm(raw)
        if not path or is_lib_test_module(path) or _skipped_non_serving(path):
            continue
        slug = slug_for_service_path(path) or _slug_for_prefix(path)
        if slug:
            slugs.add(slug)
            continue
        if path.startswith("libs/") and path.endswith(".py"):
            served = serving_services_for_lib_path(path)
            if served:
                slugs.update(served)
                continue
            name = lib_name_for_path(path)
            if name is not None and name in unserved_libs():
                continue
            unmapped.add(path)
            continue
        if path.startswith("scripts/") and path.endswith(".py"):
            name = path.rsplit("/", 1)[-1]
            if not name.startswith("test_"):
                unmapped.add(path)
            continue
        if path.startswith("services/"):
            unmapped.add(path)
    owed = ", ".join(sorted(slugs)) if slugs else "none"
    lines = [f"restart_owed: {owed}"]
    for path in sorted(unmapped):
        lines.append(f"unmapped: {path}")
    return "\n".join(lines)


def _unavailable(exc: BaseException) -> str:
    return f"restart_owed: unavailable ({type(exc).__name__})"


def restart_owed_for_range(repo: str | Path, before_sha: str, after_sha: str) -> str:
    """``restart_owed_line`` over ``git diff --name-only before..after``.

    ``before == after`` is ``restart_owed: none`` and does not run git.
    A failed diff raises; callers that record a land catch that and keep
    the land success.
    """
    before = before_sha.strip()
    after = after_sha.strip()
    if before == after:
        return "restart_owed: none"
    paths = _diff_name_only(Path(repo), before, after)
    return restart_owed_line(paths)


def _diff_name_only(repo: Path, before: str, after: str) -> list[str]:
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "diff",
            "--name-only",
            f"{before}..{after}",
        ],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode,
            proc.args,
            proc.stdout,
            proc.stderr,
        )
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _receipt_path(repo: Path, after_sha: str) -> Path:
    safe = after_sha.strip().lower()
    if _SHA_FILE_RE.fullmatch(safe) is None:
        safe = "unknown"
    return repo / "tmp" / "reviews" / "land-receipts" / f"{safe}.md"


def _receipt_body(before: str, after: str, land_path: str, block: str) -> str:
    return f"before: {before}\nafter: {after}\nland_path: {land_path}\n{block}\n"


def record_land_restart_receipt(
    repo: str | Path,
    before_sha: str,
    after_sha: str,
    land_path: str,
) -> str:
    """Write ``tmp/reviews/land-receipts/<after>.md`` and return the block.

    Diff or write failure becomes ``restart_owed: unavailable (<class>)``.
    Never raises. Call only after the master ref has moved.
    """
    root = Path(repo)
    before = before_sha.strip()
    after = after_sha.strip()
    try:
        block = restart_owed_for_range(root, before, after)
    except Exception as exc:
        block = _unavailable(exc)
    path = _receipt_path(root, after)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _receipt_body(before, after, land_path, block),
            encoding="utf-8",
        )
        return block
    except Exception as exc:
        unavailable = _unavailable(exc)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _receipt_body(before, after, land_path, unavailable),
                encoding="utf-8",
            )
        except Exception:
            pass
        return unavailable
