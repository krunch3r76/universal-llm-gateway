"""Static lint: every `record(signal, ...)` call uses a valid signal name.

Enforces the universal event-signal regex `^[a-z]+(\\.[a-z]+){1,4}$` —
2-5 dot-separated lowercase-alpha segments; no underscores, digits, or
hyphens.  Matches the constraint that `@event_factory` enforces at the
Stargate / event-service layer.  Mirroring it as a static test catches
violations at edit time rather than at packet-prep or post-deploy.

Scope: every `*.py` under `services/mcp-server/` except this test itself.

This test exists because invariant-violating signals have shipped to
production five times (cortex assertions 5464, 7715, 8010, 7191, and the
mcp.tool.pdf.read.gated/.fallback rename caught at session-review prep
for master @ 9035721f).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SIGNAL_REGEX = re.compile(r"^[a-z]+(\.[a-z]+){1,4}$")


def _signal_call_sites(tree: ast.AST) -> list[tuple[ast.AST, str | None]]:
    """Return (node, first_arg_str_or_None) for every `record(...)` call.

    Captures both `record("signal", ...)` and `*.record("signal", ...)` —
    the second covers any future receiver-method style.  Returns None
    when the first positional arg is not a string literal, so the caller
    can distinguish "non-literal signal" from "regex violation".
    """
    sites: list[tuple[ast.AST, str | None]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_record = (isinstance(func, ast.Name) and func.id == "record") or (
            isinstance(func, ast.Attribute) and func.attr == "record"
        )
        if not is_record:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            sites.append((node, first.value))
        else:
            sites.append((node, None))
    return sites


def test_record_call_sites_have_valid_signal_names() -> None:
    """Every `record("signal", ...)` literal must match SIGNAL_REGEX."""
    mcp_server_root = Path(__file__).parent
    self_path = Path(__file__).resolve()

    violations: list[str] = []
    non_literals: list[str] = []
    for py_file in mcp_server_root.rglob("*.py"):
        if py_file.resolve() == self_path:
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        rel = py_file.relative_to(mcp_server_root)
        for node, signal in _signal_call_sites(tree):
            line = getattr(node, "lineno", 0)
            if signal is None:
                non_literals.append(f"{rel}:{line} record(<non-literal>, ...)")
                continue
            if not SIGNAL_REGEX.match(signal):
                violations.append(f"{rel}:{line} {signal!r}")

    # Non-literal signal arguments are advisory — flagged for visibility, not
    # asserted, because some call sites legitimately compose signal names from
    # variables. Print to stderr via pytest capture.
    if non_literals:
        print("Advisory: non-literal record() signal arguments:")
        for entry in non_literals:
            print(f"  {entry}")

    assert not violations, (
        "Signal regex violations — must match "
        f"{SIGNAL_REGEX.pattern} (2-5 lowercase-alpha dot-segments, no "
        "underscores/digits/hyphens):\n  " + "\n  ".join(violations)
    )


def test_signal_regex_self_tests() -> None:
    """Sanity-check the regex itself against known good / bad signals."""
    good = [
        "mcp.tool.pdf.read.timeout",
        "mcp.tool.pdf.read.plaintext",
        "mcp.agentbus.dispatch",
        "events.dropped.ingest",
        "trading.market.book.top.snapshot",  # 5 segments (the cap)
    ]
    bad = [
        "mcp.tool.file.read.pdf.plaintext_gate",  # 6 segments + underscore
        "mcp.tool.pdf.read.plaintext_fallback",  # underscore in segment
        "federation.vram_request.sent",  # underscore in segment
        "mcp",  # too few segments (need ≥ 2)
        "Mcp.Tool",  # uppercase
        "mcp.tool.read.v2",  # digit in segment
        "mcp.tool.read-timeout",  # hyphen
    ]
    for s in good:
        assert SIGNAL_REGEX.match(s), f"expected match: {s!r}"
    for s in bad:
        assert not SIGNAL_REGEX.match(s), f"expected NO match: {s!r}"
