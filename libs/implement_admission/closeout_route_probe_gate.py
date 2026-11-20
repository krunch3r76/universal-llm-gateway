"""Closeout route-probe observe evaluator (Leg E2 Gate B).

observe-only: never refuses, rewrites, delays or fails a closeout

What arm 2 cannot see (docstring, from plan F11): the reach is computed on the
checkout Stargate runs from, not the worker's tree, and ``files_untracked_or_ignored``
is not read. Both lean toward under-reporting; neither produces a false
``would_require``.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.consumer_import_verify import (
    is_lib_test_module_path,
    parse_verification_tags,
    repo_root,
)
from implement_admission.propagation_row import _SYNC_RESTART_SLUG_RE
from implement_admission.route_probe_command_parse import parse_route_probe_command
from implement_admission.route_probe_reach import ROUTE_PROBE_EVIDENCE, route_probe_lib_reach

ROUTE_PROBE_REACH_BUDGET_S: float = 5.0
CANDIDATE_COMMAND_MAX_CHARS: int = 300
REASON_CODES: tuple[str, ...] = (
    "no_candidate_row",
    "not_observed",
    "nonzero_exit",
    "no_invocation_id",
    "command_shape",
)


class RouteProbeReachIncomplete(RuntimeError):
    """E1's walk returned True at or past the budget: reach not established."""


@dataclass(frozen=True, slots=True)
class RouteProbeObserveVerdict:
    would_require: bool
    satisfied: bool | None
    arms: list[str]
    required: list[dict[str, Any]]
    missing: list[dict[str, Any]]
    unmapped: list[str]
    path_prefix_only: list[str]
    reach_errors: list[dict[str, str]]
    input_defects: list[str]


def make_route_probe_reach(
    root: Path, *, budget_s: float = ROUTE_PROBE_REACH_BUDGET_S
) -> Callable[[str, str], bool]:
    """Return a ``reach`` callable that raises when a hit lands at or past ``budget_s``."""

    def reach(slug: str, lib_path: str) -> bool:
        started = time.monotonic()
        hit = route_probe_lib_reach(
            slug,
            lib_path,
            root=root,
            budget_s=budget_s,
            max_files=sys.maxsize,
        )
        if hit and time.monotonic() - started >= budget_s:
            raise RouteProbeReachIncomplete(
                f"route-probe reach not established within budget_s={budget_s}: "
                f"{slug} {lib_path}"
            )
        return hit

    return reach


def _classification_key(prefix: str, tags: dict[str, str] | None) -> str:
    if tags is None:
        return f"{prefix}:untagged"
    derived = tags.get("derived", "")
    if derived == "path_prefix":
        return f"{prefix}:path_prefix"
    return f"{prefix}:derived:{derived}"


def _names_slug(tags: dict[str, str] | None) -> bool:
    if tags is None:
        return True
    return tags.get("derived") != "path_prefix"


def _read_list_field(
    closeout: Mapping[str, Any],
    key: str,
    defects: list[str],
) -> list[Any]:
    if key not in closeout:
        return []
    val = closeout[key]
    if val is None:
        return []
    if not isinstance(val, list):
        defects.append(key)
        return []
    return val


def _grade_candidate(
    row: Mapping[str, Any], *, test_module: str
) -> tuple[bool, str, str | None]:
    """Return (satisfied, furthest_stage, parse_refusal)."""
    register = row.get("exit_code_register")
    if register != "observed":
        return False, "not_observed", None
    exit_code = row.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code != 0:
        return False, "nonzero_exit", None
    inv = row.get("invocation_id")
    if not isinstance(inv, str) or not inv.strip():
        return False, "no_invocation_id", None
    command = row.get("command")
    if not isinstance(command, str):
        return False, "command_shape", None
    accepted, refusal = parse_route_probe_command(command, test_module=test_module)
    if not accepted:
        return False, "command_shape", refusal
    return True, "satisfied", None


_STAGE_RANK = {
    "no_candidate_row": 0,
    "not_observed": 1,
    "nonzero_exit": 2,
    "no_invocation_id": 3,
    "command_shape": 4,
    "satisfied": 5,
}


def evaluate_closeout_route_probes(
    closeout: Any,
    *,
    reach: Callable[[str, str], bool] | None = None,
    evidence: Mapping[str, Any] = ROUTE_PROBE_EVIDENCE,
    root: Path | None = None,
    reach_budget_s: float = ROUTE_PROBE_REACH_BUDGET_S,
) -> RouteProbeObserveVerdict:
    """Grade closeout JSON for route-probe evidence without refusing the closeout."""
    if not isinstance(closeout, Mapping):
        return RouteProbeObserveVerdict(
            would_require=False,
            satisfied=None,
            arms=[],
            required=[],
            missing=[],
            unmapped=[],
            path_prefix_only=[],
            reach_errors=[],
            input_defects=["closeout"],
        )

    defects: list[str] = []
    propagation = _read_list_field(closeout, "propagation", defects)
    propagation_residue = _read_list_field(closeout, "propagation_residue", defects)
    verification = _read_list_field(closeout, "verification", defects)
    files_created = _read_list_field(closeout, "files_created", defects)
    files_modified = _read_list_field(closeout, "files_modified", defects)

    if reach is None:
        reach = make_route_probe_reach(
            root if root is not None else repo_root(), budget_s=reach_budget_s
        )

    propagation_rows: list[dict[str, Any]] = []
    for el in propagation:
        if not isinstance(el, dict):
            defects.append("propagation[]")
            continue
        svc = el.get("service")
        if not isinstance(svc, str):
            defects.append("propagation[]")
            continue
        reason = el.get("reason")
        if reason is not None and not isinstance(reason, str):
            reason = None
        propagation_rows.append({"service": svc, "reason": reason})

    residue_lines: list[str] = []
    for el in propagation_residue:
        if not isinstance(el, str):
            defects.append("propagation_residue[]")
            continue
        residue_lines.append(el)

    verification_rows: list[dict[str, Any]] = []
    for el in verification:
        if not isinstance(el, dict):
            defects.append("verification[]")
            continue
        cmd = el.get("command")
        if not isinstance(cmd, str):
            defects.append("verification[]")
            continue
        verification_rows.append(dict(el))

    lib_paths: list[str] = []
    seen_paths: set[str] = set()

    def _collect_lib_path(el: Any, defect_key: str) -> None:
        if not isinstance(el, str):
            defects.append(defect_key)
            return
        if not el.startswith("libs/") or not el.endswith(".py"):
            return
        if is_lib_test_module_path(el):
            return
        if el not in seen_paths:
            seen_paths.add(el)
            lib_paths.append(el)

    for el in files_created:
        _collect_lib_path(el, "files_created[]")
    for el in files_modified:
        _collect_lib_path(el, "files_modified[]")
    lib_paths.sort()

    slug_named: dict[str, list[str]] = {}
    slug_path_prefix: set[str] = set()
    unmapped: set[str] = set()

    for row in propagation_rows:
        slug = row["service"].strip().lower()
        tags = parse_verification_tags(row.get("reason"))
        if not _names_slug(tags):
            slug_path_prefix.add(slug)
            continue
        key = _classification_key("row", tags)
        slug_named.setdefault(slug, []).append(key)

    for line in residue_lines:
        match = _SYNC_RESTART_SLUG_RE.match(line.strip())
        if not match:
            continue
        slug = match.group(1).lower()
        tags = parse_verification_tags(line)
        if not _names_slug(tags):
            slug_path_prefix.add(slug)
            continue
        key = _classification_key("residue", tags)
        slug_named.setdefault(slug, []).append(key)

    for slug in list(slug_named):
        if slug not in evidence:
            unmapped.add(slug)
            del slug_named[slug]

    path_prefix_only = sorted(
        s for s in slug_path_prefix if s in evidence and not slug_named.get(s)
    )

    slug_reach: dict[str, tuple[bool | None, str | None]] = {}
    reach_errors: list[dict[str, str]] = []

    for slug in sorted(evidence.keys()):
        reached: bool | None = False
        reached_via: str | None = None
        had_error = False
        for path in lib_paths:
            try:
                if reach(slug, path):
                    reached = True
                    reached_via = path
                    break
            except Exception as exc:
                reach_errors.append(
                    {
                        "slug": slug,
                        "path": path,
                        "error_type": type(exc).__name__,
                    }
                )
                had_error = True
        if had_error and not reached:
            reached = None
        slug_reach[slug] = (reached, reached_via)

    required_slugs: set[str] = set()
    for slug in evidence:
        named = bool(slug_named.get(slug))
        reached_val, _ = slug_reach.get(slug, (False, None))
        if named or reached_val is True:
            required_slugs.add(slug)

    arms_set: set[str] = set()
    for slug in required_slugs:
        if slug_named.get(slug):
            arms_set.add("named")
        reached_val, _ = slug_reach.get(slug, (False, None))
        if reached_val is True:
            arms_set.add("reached")
    arms = sorted(arms_set)

    required_out: list[dict[str, Any]] = []
    missing_out: list[dict[str, Any]] = []

    for slug in sorted(required_slugs):
        entry = evidence[slug]
        test_module = entry.test_module
        module_rel = test_module.removeprefix("libs/")
        named = bool(slug_named.get(slug))
        named_by = sorted(set(slug_named.get(slug, [])))
        reached_val, reached_via = slug_reach.get(slug, (False, None))

        candidates = [
            r
            for r in verification_rows
            if module_rel in str(r.get("command", ""))
        ]

        satisfied = False
        evidence_invocation_id: str | None = None
        best_stage = "no_candidate_row"
        best_parse: str | None = None
        best_candidate: dict[str, Any] | None = None

        for cand in candidates:
            ok, stage, parse_ref = _grade_candidate(cand, test_module=test_module)
            if ok:
                satisfied = True
                inv = cand.get("invocation_id")
                evidence_invocation_id = inv if isinstance(inv, str) else None
                break
            if _STAGE_RANK.get(stage, 0) > _STAGE_RANK.get(best_stage, 0):
                best_stage = stage
                best_parse = parse_ref
                best_candidate = cand

        required_out.append(
            {
                "slug": slug,
                "method": entry.method,
                "path": entry.path,
                "named": named,
                "named_by": named_by,
                "reached": reached_val,
                "reached_via": reached_via,
                "satisfied": satisfied,
                "evidence_invocation_id": evidence_invocation_id,
            }
        )

        if not satisfied:
            canonical = f"pytest {test_module} -q"
            cand_cmd: str | None = None
            cand_reg: str | None = None
            cand_basis: str | None = None
            if best_candidate is not None:
                cmd = best_candidate.get("command")
                if isinstance(cmd, str):
                    cand_cmd = cmd[:CANDIDATE_COMMAND_MAX_CHARS]
                reg = best_candidate.get("exit_code_register")
                cand_reg = reg if isinstance(reg, str) else None
                basis = best_candidate.get("basis")
                cand_basis = basis if isinstance(basis, str) else None
            missing_out.append(
                {
                    "slug": slug,
                    "canonical_command": canonical,
                    "reason_code": best_stage,
                    "parse_refusal": best_parse if best_stage == "command_shape" else None,
                    "candidate_command": cand_cmd,
                    "candidate_register": cand_reg,
                    "candidate_basis": cand_basis,
                }
            )

    would_require = bool(required_slugs)
    if not would_require:
        satisfied_top: bool | None = None
    else:
        satisfied_top = all(r["satisfied"] for r in required_out)

    input_defects = sorted(set(defects))

    return RouteProbeObserveVerdict(
        would_require=would_require,
        satisfied=satisfied_top,
        arms=arms,
        required=required_out,
        missing=missing_out,
        unmapped=sorted(unmapped),
        path_prefix_only=path_prefix_only,
        reach_errors=reach_errors,
        input_defects=input_defects,
    )