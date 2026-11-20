"""Route-test reach evidence for consumer slugs (plan § 3.3 contract rows).

Maps a consumer slug to the pytest module that exercises its HTTP route, then
walks import closure from that seed module to decide whether a ``libs/`` change
reaches the route test without naming closeout refusal (Leg E2 owns that).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from implement_admission.consumer_import_verify import (
    _imports_in_file,
    _lib_file_for_module,
    _module_reached,
    is_lib_test_module_path,
    module_for_lib_path,
)


@dataclass(frozen=True, slots=True)
class RouteProbeEvidence:
    """Static evidence tying a consumer slug to a route-test module (plan § 3.3)."""

    method: str
    path: str
    test_module: str

    @property
    def canonical_command(self) -> str:
        return f"pytest {self.test_module} -q"


ROUTE_PROBE_EVIDENCE: dict[str, RouteProbeEvidence] = {
    "cdp_ask": RouteProbeEvidence(
        method="GET",
        path="/v1/project-ask/active-work",
        test_module="libs/cdp_ask/test_active_work.py",
    ),
}


def route_probe_lib_reach(
    slug: str,
    lib_path: str,
    *,
    root: Path,
    budget_s: float = 5.0,
    max_files: int = 20000,
) -> bool:
    """True when *lib_path*'s module is in the import closure seeded from the mapped test module."""
    evidence = ROUTE_PROBE_EVIDENCE.get(slug)
    if evidence is None:
        return False
    target = module_for_lib_path(lib_path)
    if target is None or is_lib_test_module_path(lib_path):
        return False
    seed = root / evidence.test_module
    if not seed.is_file():
        return False
    started = time.monotonic()
    files_read = 0

    def _budget_exhausted() -> bool:
        return files_read >= max_files or time.monotonic() - started >= budget_s

    def _read_imports(path: Path, *, module: str | None) -> frozenset[str]:
        nonlocal files_read
        if _budget_exhausted():
            return frozenset()
        files_read += 1
        return _imports_in_file(path, module=module)

    seen: set[str] = set()
    reach: set[str] = set()
    queue: deque[str] = deque(_read_imports(seed, module=module_for_lib_path(evidence.test_module)))
    if _budget_exhausted():
        return True
    while queue:
        module = queue.popleft()
        if module in seen:
            continue
        seen.add(module)
        lib_file = _lib_file_for_module(root, module)
        if lib_file is None:
            parts = module.split(".")
            while len(parts) > 1 and lib_file is None:
                parts.pop()
                lib_file = _lib_file_for_module(root, ".".join(parts))
            if lib_file is None:
                continue
            module = ".".join(parts)
            if module in reach:
                continue
        reach.add(module)
        if _module_reached(target, frozenset(reach)):
            return True
        if _budget_exhausted():
            return True
        for nxt in _read_imports(lib_file, module=module):
            if nxt not in seen:
                queue.append(nxt)
    return False
