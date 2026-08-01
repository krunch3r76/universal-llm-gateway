"""Build-time codegen entry point for cortex MCP op docs.

OpenAPI-first adapter manifest generation lives in
``cortex_store.openapi_mcp.codegen`` (``scripts/openapi_mcp_codegen.py``).
This module retains handler-signature doc harvest for the megatool facade
prose until full OpenAPI descriptor cutover.
"""

from __future__ import annotations

import argparse
import inspect
import sys
from collections.abc import Mapping, Sequence

from . import _OP_SPECS
from ._doc_gen_support import (
    ALIAS_AMBIGUOUS,
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
_TOOLS_PY = TOOLS_PY


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


def generate_blocks(op_specs: Mapping[str, str] | None = None) -> str:
    docs, _ = build_op_docs(op_specs)
    if op_specs is not None:
        # Synthetic op_specs path (tests): no live handler — still honor
        # required-name overrides so optional marking stays honest.
        tool_lines: list[str] = []
        for name in sorted(docs):
            doc = docs[name]
            alias_note = (
                f" (aliases: {', '.join(doc.aliases)})" if doc.aliases else ""
            )
            required = doc_required_names(name)
            param_str = ", ".join(
                p if p in required else f"{p}?" for p in doc.params
            )
            prose_suffix = f" — {doc.prose}" if doc.prose else ""
            tool_lines.append(f"  {name} ({param_str}){alias_note}{prose_suffix}")
        return "\n".join(tool_lines)
    return render_tool_definition_block(docs)


def apply_write(op_specs: Mapping[str, str] | None = None) -> str:
    tool_block = generate_blocks(op_specs)
    validate_generated_blocks(tool_block, expected_ops=len(op_specs or _OP_SPECS))

    tools_orig = _TOOLS_PY.read_text(encoding="utf-8")
    if START_MARKER not in tools_orig or END_MARKER not in tools_orig:
        raise RuntimeError("tools.py missing sentinel markers")

    tools_new = _replace_region(tools_orig, tool_block)
    try:
        _TOOLS_PY.write_text(tools_new, encoding="utf-8")
    except OSError:
        _TOOLS_PY.write_text(tools_orig, encoding="utf-8")
        raise
    return tool_block


def check_tree(op_specs: Mapping[str, str] | None = None) -> bool:
    tool_block = generate_blocks(op_specs)
    validate_generated_blocks(tool_block, expected_ops=len(op_specs or _OP_SPECS))
    text = _TOOLS_PY.read_text(encoding="utf-8")
    start = text.find(START_MARKER)
    end = text.find(END_MARKER)
    if start == -1 or end == -1:
        print("check failed: tools.py missing sentinels", file=sys.stderr)
        return False
    existing = text[start + len(START_MARKER) + 1 : end - 1].strip()
    if existing != tool_block.strip():
        print("check failed: drift in tools.py", file=sys.stderr)
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
