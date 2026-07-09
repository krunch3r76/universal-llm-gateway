"""Build-time codegen entry point for cortex MCP op docs."""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Mapping, Sequence

from . import _OP_SPECS
from ._doc_gen_support import (
    ALIAS_AMBIGUOUS,
    CORTEX_PY,
    END_MARKER,
    START_MARKER,
    TOOLS_PY,
    OpDoc,
    build_op_docs,
    doc_required_names,
    format_param_list,
    handler_for_op,
    validate_generated_blocks,
)

_ALIAS_AMBIGUOUS = ALIAS_AMBIGUOUS
_CORTEX_PY = CORTEX_PY
_TOOLS_PY = TOOLS_PY


def render_cortex_ops_block(docs: Mapping[str, OpDoc]) -> str:
    lines: list[str] = []
    for name in sorted(docs):
        doc = docs[name]
        alias_note = f" (aliases: {', '.join(doc.aliases)})" if doc.aliases else ""
        handler = handler_for_op(name)
        sig = inspect.signature(handler)
        param_str = format_param_list(
            sig, doc.params, required_names=doc_required_names(name)
        )
        prose_suffix = f" — {doc.prose}" if doc.prose else ""
        lines.append(
            f"                  {name:<18} ({param_str}){alias_note}{prose_suffix}"
        )
    return "\n".join(lines)


def render_tool_definition_block(docs: Mapping[str, OpDoc]) -> str:
    lines: list[str] = []
    for name in sorted(docs):
        doc = docs[name]
        handler = handler_for_op(name)
        sig = inspect.signature(handler)
        param_str = format_param_list(
            sig, doc.params, required_names=doc_required_names(name)
        )
        alias_note = f" (aliases: {', '.join(doc.aliases)})" if doc.aliases else ""
        prose_suffix = f" — {doc.prose}" if doc.prose else ""
        line = f"  {name} ({param_str}){alias_note}{prose_suffix}\n"
        escaped = (
            line.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        )
        lines.append(f'    "{escaped}"')
    return "_CORTEX_OPS_DOC = (\n" + "\n".join(lines) + "\n)"


def _replace_region(text: str, new_body: str) -> str:
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1 or end <= start:
        raise RuntimeError("sentinel markers missing or malformed")
    if not new_body:
        raise RuntimeError("generated region is empty")
    return f"{text[: start + len(START_MARKER)]}\n{new_body}\n{text[end:]}"


def generate_blocks(
    op_specs: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    docs, _ = build_op_docs(op_specs)
    if op_specs is not None:
        cortex_lines: list[str] = []
        tool_lines: list[str] = []
        for name in sorted(docs):
            doc = docs[name]
            alias_note = (
                f" (aliases: {', '.join(doc.aliases)})" if doc.aliases else ""
            )
            # Synthetic op_specs path (tests): no live handler — still honor
            # required-name overrides so optional marking stays honest.
            required = doc_required_names(name)
            param_str = ", ".join(
                p if p in required else f"{p}?" for p in doc.params
            )
            prose_suffix = f" — {doc.prose}" if doc.prose else ""
            cortex_lines.append(
                f"                  {name:<18} ({param_str}){alias_note}{prose_suffix}"
            )
            tool_lines.append(
                f"  {name} ({param_str}){alias_note}{prose_suffix}"
            )
        return "\n".join(cortex_lines), "\n".join(tool_lines)
    return render_cortex_ops_block(docs), render_tool_definition_block(docs)


def apply_write(
    op_specs: Mapping[str, str] | None = None,
) -> tuple[str, str]:
    cortex_block, tool_block = generate_blocks(op_specs)
    validate_generated_blocks(
        cortex_block, tool_block, expected_ops=len(op_specs or _OP_SPECS)
    )

    cortex_orig = _CORTEX_PY.read_text(encoding="utf-8")
    tools_orig = _TOOLS_PY.read_text(encoding="utf-8")
    if START_MARKER not in cortex_orig or END_MARKER not in cortex_orig:
        raise RuntimeError("cortex.py missing sentinel markers")
    if START_MARKER not in tools_orig or END_MARKER not in tools_orig:
        raise RuntimeError("tools.py missing sentinel markers")

    cortex_new = _replace_region(cortex_orig, cortex_block)
    tools_new = _replace_region(tools_orig, tool_block)
    try:
        _CORTEX_PY.write_text(cortex_new, encoding="utf-8")
        _TOOLS_PY.write_text(tools_new, encoding="utf-8")
    except OSError:
        _CORTEX_PY.write_text(cortex_orig, encoding="utf-8")
        _TOOLS_PY.write_text(tools_orig, encoding="utf-8")
        raise
    return cortex_block, tool_block


def check_tree(op_specs: Mapping[str, str] | None = None) -> bool:
    cortex_block, tool_block = generate_blocks(op_specs)
    validate_generated_blocks(
        cortex_block, tool_block, expected_ops=len(op_specs or _OP_SPECS)
    )
    cortex_text = _CORTEX_PY.read_text(encoding="utf-8")
    tools_text = _TOOLS_PY.read_text(encoding="utf-8")
    for label, text, expected in (
        ("cortex.py", cortex_text, cortex_block),
        ("tools.py", tools_text, tool_block),
    ):
        start = text.find(START_MARKER)
        end = text.find(END_MARKER)
        if start == -1 or end == -1:
            print(f"check failed: {label} missing sentinels", file=sys.stderr)
            return False
        existing = text[start + len(START_MARKER) + 1 : end - 1].strip()
        if existing != expected.strip():
            print(f"check failed: drift in {label}", file=sys.stderr)
            return False
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate cortex MCP op docs")
    parser.add_argument("--write", action="store_true", help="Write sentinel regions")
    parser.add_argument("--check", action="store_true", help="Verify regions match")
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.write:
        try:
            apply_write()
        except Exception as exc:
            print(f"write aborted: {exc}", file=sys.stderr)
            return 1
        return 0
    if args.check:
        return 0 if check_tree() else 1
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
