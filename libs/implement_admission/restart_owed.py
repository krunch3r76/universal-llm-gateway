"""G7 land closeout ``restart_owed:`` from the landed diff.

Slugs come from ``slug_for_service_path`` and ``_SERVICE_OWNERSHIP``
path prefixes. A ``libs/`` path uses ``serving_services_for_lib_path``
(the job set), not may-import ownership. Docs and other non-serving
trees add no slug. A services or libs path no rule maps is reported
as ``unmapped:`` rather than dropped.
"""

from __future__ import annotations

from collections.abc import Sequence

from implement_admission.propagation_row import is_lib_test_module
from implement_admission.service_lib_ownership import (
    lib_name_for_path,
    service_ownership,
    serving_services_for_lib_path,
    slug_for_service_path,
    unserved_libs,
)
from implement_admission.serving_coverage import path_serving_coverage

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
    if path.endswith(".md") and not path.startswith("services/") and not path.startswith(
        "libs/"
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
            if path_serving_coverage(path) == "unmapped":
                unmapped.add(path)
                continue
            unmapped.add(path)
            continue
        if path.startswith("services/"):
            unmapped.add(path)
    owed = ", ".join(sorted(slugs)) if slugs else "none"
    lines = [f"restart_owed: {owed}"]
    for path in sorted(unmapped):
        lines.append(f"unmapped: {path}")
    return "\n".join(lines)
