"""Shared harvest + parity helpers for cortex MCP doc codegen."""

from __future__ import annotations

import ast
import importlib
import inspect
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import _DEPRECATED_PARAM_NAMES, _INTERNAL_PARAMS, _OP_SPECS
from ._doc_required_by_op import _DOC_REQUIRED_BY_OP
from ._shared import _ENTITY_MUTABLE

START_MARKER = "# >>> AUTOGEN:cortex-ops (do not edit) >>>"
END_MARKER = "# <<< AUTOGEN:cortex-ops <<<"

_PKG_ROOT = Path(__file__).resolve().parents[3]
CORTEX_PY = _PKG_ROOT / "services/mcp-server/tools/cortex.py"
TOOLS_PY = _PKG_ROOT / "libs/agent_seat/tools.py"

ALIAS_AMBIGUOUS = "alias_canonical_ambiguous"
_DISPATCH_PKG = "cortex_store.dispatch_ops"


@dataclass(frozen=True)
class OpDoc:
    name: str
    params: tuple[str, ...]
    prose: str
    aliases: tuple[str, ...] = ()


def _resolve_handler_for_spec(
    op_specs: Mapping[str, str],
    op: str,
    spec: str | None = None,
) -> Callable[..., Any]:
    module_name, _, attr = (spec or op_specs[op]).partition(":")
    mod = importlib.import_module(f"{_DISPATCH_PKG}.{module_name}")
    return getattr(mod, attr)


def _build_handler_cache() -> dict[str, Callable[..., Any]]:
    return {op: _resolve_handler_for_spec(_OP_SPECS, op) for op in _OP_SPECS}


_HANDLER_CACHE: dict[str, Callable[..., Any]] = _build_handler_cache()


def handler_for_op(op: str) -> Callable[..., Any]:
    return _HANDLER_CACHE[op]


def first_paragraph(doc: str | None) -> str:
    if not doc:
        return ""
    return " ".join(line.strip() for line in doc.strip().splitlines() if line.strip())


def visible_signature_params(
    handler: Callable[..., Any],
    op_name: str,
    sig: inspect.Signature | None = None,
) -> list[str]:
    sig = sig or inspect.signature(handler)
    names: list[str] = []
    for pname, param in sig.parameters.items():
        if param.kind in (
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        ):
            continue
        if pname in _INTERNAL_PARAMS or pname in _DEPRECATED_PARAM_NAMES:
            continue
        names.append(pname)
    if op_name == "entity_update":
        for key in sorted(_ENTITY_MUTABLE):
            if key in _INTERNAL_PARAMS or key in names:
                continue
            names.append(key)
    return names


def format_param_list(
    sig: inspect.Signature,
    param_names: Sequence[str],
    *,
    required_names: frozenset[str] | None = None,
) -> str:
    required = required_names or frozenset()
    parts: list[str] = []
    for name in param_names:
        if name in required:
            parts.append(name)
            continue
        param = sig.parameters.get(name)
        optional = param is None or param.default is not inspect.Parameter.empty
        parts.append(f"{name}?" if optional else name)
    return ", ".join(parts)


def doc_required_names(op_name: str) -> frozenset[str]:
    return _DOC_REQUIRED_BY_OP.get(op_name, frozenset())


def group_canonical_ops(
    op_specs: Mapping[str, str],
) -> tuple[dict[str, OpDoc], dict[str, str]]:
    by_target: dict[str, list[str]] = {}
    for op, spec in op_specs.items():
        by_target.setdefault(spec, []).append(op)

    canonical_for: dict[str, str] = {}
    docs: dict[str, OpDoc] = {}

    for spec, ops in by_target.items():
        _, _, attr = spec.partition(":")
        matches = [op for op in ops if f"_op_{op}" == attr]
        if len(matches) != 1:
            raise RuntimeError(
                f"{ALIAS_AMBIGUOUS}: spec={spec!r} candidates={sorted(ops)} "
                f"matching _op_<name>={[f'_op_{op}' for op in ops]} matched={matches}"
            )
        canonical = matches[0]
        aliases = tuple(sorted(op for op in ops if op != canonical))
        handler = (
            handler_for_op(canonical)
            if op_specs is _OP_SPECS
            else _resolve_handler_for_spec(op_specs, canonical, spec)
        )
        sig = inspect.signature(handler)
        param_names = visible_signature_params(handler, canonical, sig)
        docs[canonical] = OpDoc(
            name=canonical,
            params=tuple(param_names),
            prose=first_paragraph(inspect.getdoc(handler)),
            aliases=aliases,
        )
        for op in ops:
            canonical_for[op] = canonical

    return docs, canonical_for


def build_op_docs(
    op_specs: Mapping[str, str] | None = None,
) -> tuple[dict[str, OpDoc], dict[str, str]]:
    specs = op_specs if op_specs is not None else _OP_SPECS
    docs, canonical_for = group_canonical_ops(specs)
    for op in sorted(specs):
        if canonical_for[op] not in docs:
            raise RuntimeError(f"missing canonical doc for op {op!r}")
    return docs, canonical_for


def region_body(text: str) -> str:
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1:
        raise ValueError("sentinel region not found")
    return text[start + len(START_MARKER) + 1 : end]


def parse_param_names(param_blob: str) -> set[str]:
    names: set[str] = set()
    for part in param_blob.split(","):
        token = part.strip()
        if token:
            names.add(token.rstrip("?"))
    return names


def parse_ops_block(block: str) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    alias_re = re.compile(r"\(aliases:\s*([^)]+)\)")
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = re.match(r"^(\w+)\s+\(([^)]*)\)", stripped)
        if not m:
            continue
        op = m.group(1)
        params = parse_param_names(m.group(2))
        out[op] = params
        for alias_match in alias_re.finditer(line):
            for alias in alias_match.group(1).split(","):
                out[alias.strip()] = params
    return out


def decode_tools_doc_block(tool_block: str) -> str:
    if "_CORTEX_OPS_DOC" not in tool_block:
        return tool_block
    module = ast.parse(tool_block)
    assign = module.body[0]
    if not isinstance(assign, ast.Assign):
        raise RuntimeError("tools.py generated block must assign _CORTEX_OPS_DOC")
    value = ast.literal_eval(assign.value)
    if isinstance(value, str):
        return value
    if isinstance(value, tuple):
        return "".join(str(part) for part in value)
    raise RuntimeError("tools.py generated block has unexpected _CORTEX_OPS_DOC shape")


def count_documented_ops(block: str) -> set[str]:
    names: set[str] = set()
    alias_re = re.compile(r"\(aliases:\s*([^)]+)\)")
    for line in block.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^(\w+)\s+\(", stripped)
        if m:
            names.add(m.group(1))
        for alias_match in alias_re.finditer(line):
            for part in alias_match.group(1).split(","):
                names.add(part.strip())
    return names


def validate_generated_blocks(
    cortex_block: str,
    tool_block: str,
    *,
    expected_ops: int | None = None,
) -> None:
    expected = expected_ops if expected_ops is not None else len(_OP_SPECS)
    tool_text = (
        decode_tools_doc_block(tool_block)
        if "_CORTEX_OPS_DOC" in tool_block
        else tool_block
    )
    for label, block in (("cortex.py", cortex_block), ("tools.py", tool_text)):
        if START_MARKER in block or END_MARKER in block:
            raise RuntimeError(f"{label}: generated body must not include sentinels")
        if not block.strip():
            raise RuntimeError(f"{label}: generated region is empty")
        names = count_documented_ops(block)
        if len(names) != expected:
            missing = set(_OP_SPECS) - names
            extra = names - set(_OP_SPECS)
            raise RuntimeError(
                f"{label}: op count {len(names)} != {expected}; "
                f"missing={sorted(missing)[:5]} extra={sorted(extra)[:5]}"
            )


def parse_cortex_ops_from_source(text: str) -> dict[str, set[str]]:
    return parse_ops_block(region_body(text))


def parse_tool_definition_ops_from_source(text: str) -> dict[str, set[str]]:
    return parse_ops_block(decode_tools_doc_block(region_body(text).strip()))


def expected_visible_params(op: str) -> set[str]:
    return set(visible_signature_params(handler_for_op(op), op))
