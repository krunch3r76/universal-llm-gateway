#!/usr/bin/env python3
"""Lint FastAPI route handlers against `JSONResponse`-in-return-annotation.

Rejected pattern::

    @router.get("", response_model=AssertionList)
    def list_assertions(...) -> AssertionList | JSONResponse: ...

FastAPI inspects the return annotation at app startup and raises
FastAPIError because `JSONResponse` isn't a pydantic model. The canonical
fix is to drop `JSONResponse` from the annotation (FastAPI passes
`Response` subclasses through untouched regardless). See assertion 7951
and the near-miss on agent-bus thread 882 turn 13 — that near-miss
happened *with* the warning visible on the boot card, which is why this
gate moves the enforcement from attention-layer to import/CI layer.

Usage
-----
    scripts/lint-fastapi-annotations.py [FILES...]
    scripts/lint-fastapi-annotations.py --staged

With no arguments: scan every `**/routes/*.py` file tracked in the repo.
With `--staged`: scan only the staged subset (for pre-commit hooks).

Exit status
-----------
* 0 — clean
* 1 — violations found (diagnostics printed to stderr)
* 2 — usage error / file read failure
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_ROUTER_DECORATOR_ATTRS = frozenset(
    {"get", "post", "put", "patch", "delete", "head", "options", "api_route"}
)
_FORBIDDEN_NAME = "JSONResponse"


def _is_route_decorator(dec: ast.expr) -> bool:
    """Return True if ``dec`` looks like ``@router.get(...)`` or ``@app.post(...)``."""
    call = dec if isinstance(dec, ast.Call) else None
    if call is None:
        return False
    func = call.func
    if not isinstance(func, ast.Attribute):
        return False
    return func.attr in _ROUTER_DECORATOR_ATTRS


def _annotation_references_jsonresponse(node: ast.expr) -> bool:
    """Walk ``node`` and return True iff the name ``JSONResponse`` appears.

    Catches bare (`JSONResponse`), union-operator (`X | JSONResponse`),
    typing.Union (`Union[X, JSONResponse]`), and Optional/Annotated nestings.
    """
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and child.id == _FORBIDDEN_NAME:
            return True
        if isinstance(child, ast.Attribute) and child.attr == _FORBIDDEN_NAME:
            return True
    return False


def _check_function(
    func: ast.FunctionDef | ast.AsyncFunctionDef, path: Path
) -> list[str]:
    if not any(_is_route_decorator(d) for d in func.decorator_list):
        return []
    if func.returns is None:
        return []
    if not _annotation_references_jsonresponse(func.returns):
        return []
    annotation_src = ast.unparse(func.returns)
    return [
        f"{path}:{func.lineno}:{func.col_offset + 1}: "
        f"FA001 route handler `{func.name}` has `JSONResponse` in return "
        f"annotation (`-> {annotation_src}`). FastAPI passes Response "
        f"subclasses through untouched — drop `JSONResponse` from the "
        f"annotation and keep `response_model=...` on the decorator. "
        f"See assertion 7951."
    ]


def scan_file(path: Path) -> list[str]:
    try:
        source = path.read_text()
    except OSError as exc:
        return [f"{path}:0:0: read error: {exc}"]
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno or 0}:{exc.offset or 0}: syntax error: {exc.msg}"]
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            violations.extend(_check_function(node, path))
    return violations


def _git_tracked_route_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "**/routes/*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [REPO_ROOT / line for line in out.stdout.splitlines() if line.strip()]


def _git_staged_route_files() -> list[Path]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACMR"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [
        REPO_ROOT / line
        for line in out.stdout.splitlines()
        if "/routes/" in line and line.endswith(".py")
    ]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Files to scan (default: all tracked routes/*.py)",
    )
    parser.add_argument(
        "--staged",
        action="store_true",
        help="Scan only files staged for commit under any routes/ directory.",
    )
    args = parser.parse_args(argv)

    if args.files and args.staged:
        print(
            "error: --staged is exclusive with explicit file arguments", file=sys.stderr
        )
        return 2

    if args.staged:
        files = _git_staged_route_files()
    elif args.files:
        files = [f if f.is_absolute() else REPO_ROOT / f for f in args.files]
    else:
        files = _git_tracked_route_files()

    if not files:
        return 0

    violations: list[str] = []
    for path in files:
        if not path.is_file():
            continue
        violations.extend(scan_file(path))

    if violations:
        for v in violations:
            print(v, file=sys.stderr)
        print(
            f"\n{len(violations)} violation(s). Fix the annotations and re-run.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
