"""Tree-walk helpers the reporter uses to form derived denominators.

Callers: coats.py. These functions exist so a hand-list can be diffed against
something the reporter computed, rather than trusted as complete.
"""

from __future__ import annotations

import ast
from pathlib import Path

CANDIDATE_ROOTS = (
    "services/mcp-server/tools",
    "libs/claude_bundles",
)
VISION_MARKERS = ("vision_required", "VISION_REQUIRED", "vision_field_missing")
ENFORCEMENT_MEMBERS_IN_PROSE = ("implement", "investigate", "seed", "recon")


def _eval_str_set(node: ast.AST) -> set[str] | None:
    if not isinstance(node, ast.Call):
        return None
    func = node.func
    name = func.id if isinstance(func, ast.Name) else None
    if name not in {"frozenset", "set"}:
        return None
    if not node.args:
        return set()
    arg = node.args[0]
    if not isinstance(arg, (ast.Set, ast.List, ast.Tuple)):
        return None
    out: set[str] = set()
    for elt in arg.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.add(elt.value)
        else:
            return None
    return out


def extract_frozenset_assign(path: Path, name: str) -> set[str] | None:
    """Return string members of a module-level frozenset assignment, or None."""
    if not path.is_file():
        return None
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        targets: list[ast.AST] = []
        value: ast.AST | None = None
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if value is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return _eval_str_set(value)
    return None


def disclosure_candidates(repo: Path) -> set[str]:
    """Return repo-relative paths under candidate roots that mention vision-enforcement tokens."""
    found: set[str] = set()
    for root in CANDIDATE_ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(marker in text for marker in VISION_MARKERS):
                found.add(path.relative_to(repo).as_posix())
    return found


def prose_omits_enforcement_members(repo: Path, rel: str) -> list[str]:
    """Findings when a listed disclosure site omits vision tokens or member names."""
    path = repo / rel
    if not path.is_file():
        return [f"missing disclosure site: {rel}"]
    text = path.read_text(encoding="utf-8", errors="replace")
    if "vision:" not in text and "vision_required" not in text:
        return [f"{rel}: no vision disclosure token"]
    omitted = [name for name in ENFORCEMENT_MEMBERS_IN_PROSE if name not in text]
    if omitted:
        return [f"{rel}: vision prose omits {omitted}"]
    return []
