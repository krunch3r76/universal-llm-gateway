"""Doc-to-CLI conformance: additive tag flags mirror MCP add_tags/remove_tags."""

from __future__ import annotations

import ast
from pathlib import Path


def test_cli_exposes_add_and_remove_tag_flags() -> None:
    cli_path = Path(__file__).resolve().parent / "agent-bus"
    source = cli_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    update_parser = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "add_parser":
            if node.args and isinstance(node.args[0], ast.Constant):
                if node.args[0].value == "update-thread":
                    update_parser = node
                    break
    assert update_parser is not None, "update-thread subparser missing"
    flag_names = {
        kw.arg
        for kw in update_parser.keywords
        if kw.arg == "dest" and isinstance(kw.value, ast.Constant)
    }
    # argparse stores dest on add_argument nodes under the parser — scan source tokens
    assert "--add-tag" in source
    assert "--remove-tag" in source
    assert 'dest="add_tags"' in source or "dest='add_tags'" in source
    assert 'dest="remove_tags"' in source or "dest='remove_tags'" in source
    assert '"add_tags"' in source
    assert '"remove_tags"' in source
