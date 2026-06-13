"""Deterministic extraction of @event_factory event vocabulary.

Pure functions — no event bus, no async, no I/O beyond reading source files
(mirrors libs/doc_extraction's pure-function contract).
"""

from __future__ import annotations

import ast
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
WALK_ROOTS: tuple[str, ...] = ("services", "libs", "systems")
ROLE_DEFAULT = (
    "observation"  # Event.role default (libs/universal_event_bus/events/event.py)
)
SCOPE_DEFAULT = "global"  # Event.scope default
# Inline annotation grammar:  # event-catalog: signal=foo.bar[, payload=k1|k2]
_ANNOTATION_RE = re.compile(
    r"#\s*event-catalog:\s*signal=(?P<signal>[\w.]+)"
    r"(?:\s*,\s*payload=(?P<payload>[\w|]+))?"
)


@dataclass(frozen=True)
class FactoryRecord:
    signal: str
    domain: str
    required_keys: tuple[str, ...]
    optional_keys: tuple[str, ...]
    role: str
    scope: str
    role_is_default: bool
    scope_is_default: bool
    factory_name: str
    description: str
    source_path: str
    lineno: int
    signal_dynamic: bool
    payload_dynamic: bool
    resolution: str  # "static" | "const_ref" | "annotated" | "exceptions" | "dynamic"


def load_exceptions(path: Path) -> dict[str, dict]:
    """Curated escape hatch: {"<source_path>::<factory>": {"signal":..,"required":[...]}}."""
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def extract_factories(
    roots: tuple[str, ...] = WALK_ROOTS,
    *,
    exceptions: dict[str, dict] | None = None,
) -> list[FactoryRecord]:
    exceptions = exceptions or {}
    records: list[FactoryRecord] = []
    for root in roots:
        for path in sorted((PROJECT_ROOT / root).rglob("*.py")):
            if path.name == "__init__.py":
                continue
            source = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(source, filename=str(path))
            except SyntaxError:
                continue
            rel = str(path.relative_to(PROJECT_ROOT))
            lines = source.splitlines()
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                    continue
                if not _has_event_factory_decorator(node):
                    continue
                rec = _record_for(node, path, rel, lines, exceptions)
                if rec is not None:
                    records.append(rec)
    return records


def _has_event_factory_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "event_factory":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "event_factory":
            return True
    return False


def _find_event_call(node: ast.AST) -> ast.Call | None:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Name)
            and sub.func.id == "Event"
        ):
            return sub
    return None


def _kw(call: ast.Call, name: str) -> ast.expr | None:
    return next((k.value for k in call.keywords if k.arg == name), None)


def _const_str(expr: ast.expr | None) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    return None


def _scan_annotation(lines: list[str], node: ast.FunctionDef | ast.AsyncFunctionDef):
    """Look for a # event-catalog: comment in the decorator/def header region."""
    start = (
        min(d.lineno for d in node.decorator_list)
        if node.decorator_list
        else node.lineno
    )
    for i in range(start - 1, node.lineno + 1):
        if 0 <= i - 1 < len(lines):
            m = _ANNOTATION_RE.search(lines[i - 1])
            if m:
                return m
    return None


def _resolve_payload(
    call: ast.Call, funcdef: ast.AST
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """Return (required_keys, optional_keys, payload_dynamic)."""
    payload = _kw(call, "payload")
    if isinstance(payload, ast.Dict):
        req = tuple(
            k.value
            for k in payload.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        )
        dynamic = len(req) != len(payload.keys)
        return req, (), dynamic
    if isinstance(payload, ast.Name):
        return _resolve_payload_var(payload.id, funcdef)
    return (), (), True


def _resolve_payload_var(
    name: str, funcdef: ast.AST
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    required: tuple[str, ...] = ()
    optional: list[str] = []
    for stmt in ast.walk(funcdef):
        if isinstance(stmt, ast.Assign):
            if (
                len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and stmt.targets[0].id == name
                and isinstance(stmt.value, ast.Dict)
            ):
                required = tuple(
                    k.value
                    for k in stmt.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
        elif (
            isinstance(stmt, ast.AnnAssign)
            and isinstance(stmt.target, ast.Name)
            and stmt.target.id == name
            and isinstance(stmt.value, ast.Dict)
        ):
            required = tuple(
                k.value
                for k in stmt.value.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            )
        if (
            isinstance(stmt, ast.Assign)
            and len(stmt.targets) == 1
            and isinstance(stmt.targets[0], ast.Subscript)
            and isinstance(stmt.targets[0].value, ast.Name)
            and stmt.targets[0].value.id == name
        ):
            key = _const_str(stmt.targets[0].slice)
            if key:
                optional.append(key)
    payload_dynamic = not required
    return required, tuple(optional), payload_dynamic


def _record_for(node, path: Path, rel, lines, exceptions) -> FactoryRecord | None:
    call = _find_event_call(node)
    if call is None:
        return None
    factory = node.name
    key = f"{rel}::{factory}"
    doc = ast.get_docstring(node)
    description = doc.strip().splitlines()[0] if doc else ""

    from .signals import resolve_signal

    sig_expr = _kw(call, "signal")
    signal = _const_str(sig_expr)
    resolution = "static"
    signal_dynamic = False
    req, opt, payload_dynamic = _resolve_payload(call, node)

    if signal is None and isinstance(sig_expr, ast.Name):
        signal = resolve_signal(sig_expr.id, path)
        if signal is not None:
            resolution = "const_ref"

    if signal is None:
        ann = _scan_annotation(lines, node)
        if ann:
            signal = ann.group("signal")
            resolution = "annotated"
            if ann.group("payload"):
                req, opt, payload_dynamic = (
                    tuple(ann.group("payload").split("|")),
                    (),
                    False,
                )
        elif key in exceptions:
            signal = exceptions[key]["signal"]
            req = tuple(exceptions[key].get("required", []))
            resolution = "exceptions"
            payload_dynamic = False
        else:
            signal = f"<dynamic:{factory}>"
            signal_dynamic = True
            resolution = "dynamic"

    role = _const_str(_kw(call, "role"))
    scope = _const_str(_kw(call, "scope"))
    domain = signal.split(".")[0] if not signal_dynamic else "dynamic"

    return FactoryRecord(
        signal=signal,
        domain=domain,
        required_keys=req,
        optional_keys=opt,
        role=role or ROLE_DEFAULT,
        scope=scope or SCOPE_DEFAULT,
        role_is_default=role is None,
        scope_is_default=scope is None,
        factory_name=factory,
        description=description,
        source_path=rel,
        lineno=node.lineno,
        signal_dynamic=signal_dynamic,
        payload_dynamic=payload_dynamic,
        resolution=resolution,
    )
