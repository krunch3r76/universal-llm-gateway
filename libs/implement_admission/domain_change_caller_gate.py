"""Gate A: domain-change caller inventory and route-test notice at implement admit."""

from __future__ import annotations

import ast
import hashlib
import os
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from implement_admission.consumer_import_verify import (
    _resolve_relative,
    is_lib_test_module_path,
    module_for_lib_path,
)
from implement_admission.route_probe_reach import (
    ROUTE_PROBE_EVIDENCE,
    route_probe_lib_reach,
)

CALLER_INVENTORY_UNDECLARED = "caller_inventory_undeclared"
CALLER_INVENTORY_MISSING = "caller_inventory_missing"
CALLER_INVENTORY_UNAVAILABLE = "caller_inventory_unavailable"
ROUTE_PROBE_UNDECLARED = "route_probe_undeclared"
DEFAULT_BUDGET_S = 5.0
DEFAULT_MAX_FILES = 20000
MESSAGE_SITE_CAP = 20
PRUNED_DIR_NAMES = frozenset({"__pycache__", "node_modules", "dist"})
_SHARE_PREFIX = "workspaces://universal-llm-gateway/"
_WALK_ROOTS = ("libs", "services")
_SHA_EMPTY = hashlib.sha256(b"").hexdigest()
InventoryStatus = Literal["complete", "incomplete", "no_lib_paths"]


@dataclass(frozen=True, slots=True)
class ImporterSite:
    module: str
    path: str
    line: int
    names: tuple[str, ...]
    in_files_expected: bool


@dataclass(frozen=True, slots=True)
class ImporterInventory:
    status: InventoryStatus
    modules: tuple[str, ...]
    eligible_paths: tuple[str, ...]
    sites: tuple[ImporterSite, ...]
    scanned_files: int
    skipped_unparseable: int
    sha256: str
    incomplete_reason: str | None = None


@dataclass(frozen=True, slots=True)
class CallerAdmitVerdict:
    admitted: bool
    code: str | None = None
    reason: str | None = None


def normalize_expected_path(entry: object) -> str | None:
    if not isinstance(entry, str):
        return None
    text = entry.strip().strip("`").strip()
    if text.startswith(_SHARE_PREFIX):
        text = text[len(_SHARE_PREFIX) :]
    text = text.replace("\\", "/")
    if not text or "://" in text or text.startswith("/"):
        return None
    if any(seg == ".." for seg in text.split("/")):
        return None
    return text


def _is_test_module(rel: str) -> bool:
    if is_lib_test_module_path(rel):
        return True
    base = rel.rsplit("/", 1)[-1]
    return base.startswith("test_") or base.endswith("_test.py") or "tests" in rel.split("/")


def _candidate_lib_paths(entries: Iterable[object]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        norm = normalize_expected_path(entry)
        if (
            norm is None
            or not norm.startswith("libs/")
            or not norm.endswith(".py")
            or module_for_lib_path(norm) is None
            or _is_test_module(norm)
            or norm in seen
        ):
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _match_import_sites(
    tree: ast.AST, rel: str, targets: dict[str, str], expected: set[str]
) -> dict[tuple[str, str, int], ImporterSite]:
    sites: dict[tuple[str, str, int], ImporterSite] = {}
    importer_mod = module_for_lib_path(rel) if rel.startswith("libs/") else None
    if importer_mod and rel.endswith("/__init__.py"):
        importer_mod = f"{importer_mod}.__init__"

    def _add(module: str, node: ast.AST, names: tuple[str, ...]) -> None:
        key = (module, rel, node.lineno)
        if key not in sites:
            sites[key] = ImporterSite(module, rel, node.lineno, names, rel in expected)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                for module in targets:
                    if alias.name == module or alias.name.startswith(module + "."):
                        _add(module, node, ())
        elif isinstance(node, ast.ImportFrom):
            if node.level >= 1 and importer_mod is None:
                continue
            resolved = node.module
            if node.level >= 1 and importer_mod:
                resolved = _resolve_relative(importer_mod, "." * node.level, node.module)
            if resolved is None and node.level >= 1:
                continue
            for module in targets:
                parent, _, last = module.rpartition(".")
                if resolved == module or (resolved and resolved.startswith(module + ".")):
                    _add(module, node, tuple(a.name for a in node.names))
                elif resolved == parent and any(a.name == last for a in node.names):
                    _add(module, node, (last,))
    return sites


def build_importer_inventory(
    lib_paths: Iterable[object],
    *,
    repo_root: Path,
    files_expected: Iterable[object],
    budget_s: float = DEFAULT_BUDGET_S,
    max_files: int = DEFAULT_MAX_FILES,
) -> ImporterInventory:
    candidates = _candidate_lib_paths(lib_paths)
    empty = ImporterInventory("no_lib_paths", (), (), (), 0, 0, _SHA_EMPTY)
    if not candidates:
        return empty
    if not (repo_root / "libs").is_dir() or not (repo_root / "services").is_dir():
        return ImporterInventory("incomplete", (), (), (), 0, 0, _SHA_EMPTY, "root_not_checkout")
    eligible = [p for p in candidates if (repo_root / p).is_file()]
    if not eligible:
        return empty
    targets = {module_for_lib_path(p): p for p in eligible}
    modules = tuple(sorted(targets))
    expected = {n for n in map(normalize_expected_path, files_expected) if n}
    started, scanned, skipped, site_map = time.monotonic(), 0, 0, {}
    incomplete_reason: str | None = None
    last_segments = {m.rpartition(".")[2] for m in targets}
    for root_name in _WALK_ROOTS:
        walk_root = repo_root / root_name
        if not walk_root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(walk_root, topdown=True):
            dirnames[:] = [
                d for d in sorted(dirnames) if not d.startswith(".") and d not in PRUNED_DIR_NAMES
            ]
            for fname in sorted(f for f in filenames if f.endswith(".py")):
                rel = Path(dirpath, fname).relative_to(repo_root).as_posix()
                if _is_test_module(rel) or rel in eligible:
                    continue
                if time.monotonic() - started >= budget_s:
                    incomplete_reason = "budget"
                    break
                if scanned >= max_files:
                    incomplete_reason = "file_cap"
                    break
                try:
                    text = (repo_root / rel).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                scanned += 1
                if not any(seg in text for seg in last_segments):
                    continue
                try:
                    tree = ast.parse(text)
                except (SyntaxError, ValueError):
                    skipped += 1
                    continue
                site_map.update(_match_import_sites(tree, rel, targets, expected))
            if incomplete_reason:
                break
        if incomplete_reason:
            break
    sites = tuple(sorted(site_map.values(), key=lambda s: (s.path, s.line, s.module)))
    payload = "\n".join(
        f"{s.path}:{s.line}:{s.module}:{','.join(s.names)}:{int(s.in_files_expected)}"
        for s in sites
    )
    return ImporterInventory(
        "complete" if incomplete_reason is None else "incomplete",
        modules,
        tuple(eligible),
        sites,
        scanned,
        skipped,
        hashlib.sha256(payload.encode()).hexdigest(),
        incomplete_reason,
    )


def _contract_change(raw: object) -> bool | None:
    if raw is True or (isinstance(raw, str) and raw.strip().lower() == "true"):
        return True
    if raw is False or (isinstance(raw, str) and raw.strip().lower() == "false"):
        return False
    return None


def _caller_inventory_entries(raw: object) -> dict[str, str]:
    if not isinstance(raw, list):
        return {}
    out: dict[str, str] = {}
    for item in raw:
        if not isinstance(item, str):
            continue
        sep = " — " if " — " in item else " - " if " - " in item else None
        if not sep:
            continue
        path_part, reason = item.split(sep, 1)
        path, reason = normalize_expected_path(path_part), reason.strip()
        if path and reason:
            out[path] = reason
    return out


def _route_probe_tests(raw: object) -> set[str]:
    if not isinstance(raw, list):
        return set()
    return {n for item in raw if isinstance(item, str) and (n := normalize_expected_path(item))}


def evaluate_domain_change_caller_admit(
    *,
    todo_id: str,
    files_expected: Sequence[object],
    attributes: Mapping[str, object],
    repo_root: Path,
    budget_s: float = DEFAULT_BUDGET_S,
    max_files: int = DEFAULT_MAX_FILES,
) -> CallerAdmitVerdict:
    inv = build_importer_inventory(
        files_expected, repo_root=repo_root, files_expected=files_expected,
        budget_s=budget_s, max_files=max_files,
    )
    if inv.status == "incomplete":
        why = {
            "budget": "time budget reached",
            "file_cap": "file cap reached",
        }.get(inv.incomplete_reason or "", "the repository root has no libs/ or services/ directory")
        return CallerAdmitVerdict(
            False,
            CALLER_INVENTORY_UNAVAILABLE,
            f"{todo_id}: the caller inventory could not be completed ({why}; "
            f"{inv.scanned_files} file(s) scanned under {repo_root}, budget "
            f"{budget_s:g} s / {max_files} files). Implement admission is refused "
            "until the scan completes.",
        )
    outside = [s for s in inv.sites if not s.in_files_expected]
    if outside:
        declared = _contract_change(attributes.get("domain_contract_change"))
        if declared is None:
            n = len(outside)
            sites_csv = ", ".join(f"{s.path}:{s.line}" for s in outside[:MESSAGE_SITE_CAP])
            more = f" and {n - MESSAGE_SITE_CAP} more" if n > MESSAGE_SITE_CAP else ""
            return CallerAdmitVerdict(
                False,
                CALLER_INVENTORY_UNDECLARED,
                f"{todo_id}: files_expected changes lib module(s) {', '.join(inv.modules)} "
                f"imported at {n} site(s) outside files_expected: {sites_csv}{more}. "
                'Declare attributes.domain_contract_change: "false" when the change keeps '
                "the helper's accepted inputs, return values and raised errors, or \"true\" "
                'with one attributes.caller_inventory entry per outside path, written as '
                '"path — reason".',
            )
        if declared is True:
            entries = _caller_inventory_entries(attributes.get("caller_inventory"))
            missing = sorted({s.path for s in outside} - set(entries))
            if missing:
                return CallerAdmitVerdict(
                    False,
                    CALLER_INVENTORY_MISSING,
                    f"{todo_id}: domain_contract_change is true but attributes.caller_inventory "
                    f"has no reasoned entry for {', '.join(missing)}. Add one entry per path, "
                    'written as "path — reason".',
                )
    required: dict[str, str] = {}
    for slug in sorted(ROUTE_PROBE_EVIDENCE):
        for lib_path in inv.eligible_paths:
            if route_probe_lib_reach(slug, lib_path, root=repo_root, budget_s=budget_s, max_files=max_files):
                required[slug] = lib_path
                break
    declared_tests = _route_probe_tests(attributes.get("route_probe_tests"))
    missing_slugs = [s for s in required if ROUTE_PROBE_EVIDENCE[s].test_module not in declared_tests]
    if missing_slugs:
        parts = [
            f"{slug} ({ROUTE_PROBE_EVIDENCE[slug].method} {ROUTE_PROBE_EVIDENCE[slug].path}) "
            f"through {required[slug]}: list {ROUTE_PROBE_EVIDENCE[slug].test_module} "
            f"in attributes.route_probe_tests and put "
            f"`{ROUTE_PROBE_EVIDENCE[slug].canonical_command}` in the packet's verification steps"
            for slug in missing_slugs
        ]
        return CallerAdmitVerdict(
            False,
            ROUTE_PROBE_UNDECLARED,
            f"{todo_id}: files_expected reaches a consumer's route test. " + "; ".join(parts) + ".",
        )
    return CallerAdmitVerdict(admitted=True)