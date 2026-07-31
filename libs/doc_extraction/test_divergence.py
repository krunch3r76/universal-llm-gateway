"""Unit tests for libs/doc_extraction/divergence.py.

Covers:
- GATE: _signature() shape invariant (def present, trailing colon stripped)
- detect_symbol_divergence: phantom param, return-on-None, clean, no-docstring
- detect_inventory_divergence: multi-symbol scan
"""

import ast

from doc_extraction import detect_inventory_divergence, detect_symbol_divergence
from doc_extraction.divergence import _signature_facts
from doc_extraction.inventory import _signature

# ---------------------------------------------------------------------------
# GATE: regression-pin on _signature() shape
# ---------------------------------------------------------------------------


class _FakeNode:
    """Minimal tree-sitter node stub for _signature shape assertion."""

    def __init__(self, src: bytes, start: int, end: int, body_start: int | None = None):
        self._src = src
        self.start_byte = start
        self.end_byte = end
        self._body_start = body_start

    def child_by_field_name(self, name: str):
        if name == "body" and self._body_start is not None:
            return _BodyNode(self._body_start)
        return None


class _BodyNode:
    def __init__(self, start: int):
        self.start_byte = start


def test_signature_shape_starts_with_def():
    """GATE: _signature always starts with 'def' or 'async def', trailing colon stripped."""
    source = b"def foo(a: int, b: str = 3) -> None:\n    pass\n"
    # body starts at '\n    pass\n' — offset 37 (the '\n' after ':')
    body_start = source.index(b":\n") + 1
    node = _FakeNode(source, 0, len(source), body_start)
    sig = _signature(node, source)
    # Must start with 'def'
    assert sig.startswith("def "), f"Expected 'def ...', got: {sig!r}"
    # Must NOT end with ':'
    assert not sig.endswith(":"), f"Unexpected trailing colon: {sig!r}"
    # ast.parse(sig + ': ...') must not raise
    ast.parse(sig + ": ...")


def test_signature_facts_parses_def():
    """_signature_facts correctly extracts params and return annotation."""
    params, ret = _signature_facts("def foo(a: int, b: str = 'x') -> None")
    assert "a" in params
    assert "b" in params
    assert ret == "None"


def test_signature_facts_async_def():
    params, ret = _signature_facts("async def bar(x, y) -> str")
    assert params == {"x", "y"}
    assert ret == "str"


def test_signature_facts_strips_self_cls():
    params, ret = _signature_facts("def method(self, a: int) -> None")
    assert "self" not in params
    assert "a" in params


def test_signature_facts_non_def_returns_empty():
    params, ret = _signature_facts("class Foo(Base):")
    assert params == set()
    assert ret is None


def test_signature_facts_no_return_annotation():
    params, ret = _signature_facts("def baz(x)")
    assert "x" in params
    assert ret is None


# ---------------------------------------------------------------------------
# detect_symbol_divergence — drift fixtures
# ---------------------------------------------------------------------------


def test_phantom_param_google_style():
    """Documented param absent from signature → param_absent_from_signature finding."""
    findings = detect_symbol_divergence(
        path="foo.py",
        name="do_thing",
        signature="def do_thing(a: int) -> None",
        docstring=(
            "Do the thing.\n\n"
            "Args:\n"
            "    a: First arg.\n"
            "    phantom: This param does not exist in signature.\n"
        ),
        line=10,
    )
    assert len(findings) == 1
    f = findings[0]
    assert f["kind"] == "param_absent_from_signature"
    assert "phantom" in f["detail"]
    assert f["path"] == "foo.py"
    assert f["name"] == "do_thing"


def test_phantom_param_rest_style():
    """reST-style :param docs detected for absent signature param."""
    findings = detect_symbol_divergence(
        path="bar.py",
        name="compute",
        signature="def compute(x: float) -> float",
        docstring=":param x: Input value.\n:param ghost: Missing from sig.\n:returns: Result.",
        line=5,
    )
    kinds = {f["kind"] for f in findings}
    assert "param_absent_from_signature" in kinds
    assert any("ghost" in f["detail"] for f in findings)


def test_return_documented_on_none():
    """Return documented but signature is -> None → return_documented_on_none."""
    findings = detect_symbol_divergence(
        path="baz.py",
        name="side_effect",
        signature="def side_effect(x: int) -> None",
        docstring=(
            "Do something with side effects.\n\n"
            "Args:\n"
            "    x: Input.\n\n"
            "Returns:\n"
            "    Nothing useful.\n"
        ),
        line=20,
    )
    assert len(findings) == 1
    assert findings[0]["kind"] == "return_documented_on_none"


def test_clean_symbol_no_findings():
    """Clean symbol with matching params and no documented return on None."""
    findings = detect_symbol_divergence(
        path="clean.py",
        name="well_documented",
        signature="def well_documented(a: int, b: str) -> str",
        docstring=(
            "Return a formatted string.\n\n"
            "Args:\n"
            "    a: An integer.\n"
            "    b: A string.\n\n"
            "Returns:\n"
            "    Formatted result.\n"
        ),
        line=1,
    )
    assert findings == []


def test_no_docstring_no_findings():
    """Symbol with empty docstring produces no findings."""
    findings = detect_symbol_divergence(
        path="no_doc.py",
        name="undocumented",
        signature="def undocumented(x, y)",
        docstring="",
        line=1,
    )
    assert findings == []


def test_whitespace_only_docstring_no_findings():
    findings = detect_symbol_divergence(
        path="ws.py",
        name="ws_func",
        signature="def ws_func(x)",
        docstring="   \n   ",
        line=1,
    )
    assert findings == []


def test_self_cls_not_reported_as_phantom():
    """self and cls are never reported as phantom params."""
    findings = detect_symbol_divergence(
        path="cls.py",
        name="MyClass.method",
        signature="def method(self, value: int) -> None",
        docstring=("Set a value.\n\nArgs:\n    value: The value.\n"),
        line=3,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# detect_inventory_divergence — multi-symbol scan
# ---------------------------------------------------------------------------


def test_detect_inventory_divergence_empty():
    assert detect_inventory_divergence({}) == []


def test_detect_inventory_divergence_functions():
    inventory = {
        "functions": [
            {
                "path": "mod.py",
                "name": "good_fn",
                "signature": "def good_fn(a: int) -> None",
                "docstring": "Args:\n    a: OK.",
                "line": 1,
            },
            {
                "path": "mod.py",
                "name": "bad_fn",
                "signature": "def bad_fn(a: int) -> None",
                "docstring": "Args:\n    a: OK.\n    phantom: Missing.\n",
                "line": 10,
            },
        ]
    }
    findings = detect_inventory_divergence(inventory)
    assert len(findings) == 1
    assert findings[0]["name"] == "bad_fn"
    assert findings[0]["kind"] == "param_absent_from_signature"


def test_detect_inventory_divergence_class_methods():
    inventory = {
        "classes": [
            {
                "path": "cls.py",
                "name": "MyClass",
                "line": 1,
                "methods": [
                    {
                        "name": "do_work",
                        "signature": "def do_work(self, x: int) -> None",
                        "docstring": "Returns:\n    Nothing.\n",
                        "line": 5,
                    }
                ],
            }
        ]
    }
    findings = detect_inventory_divergence(inventory)
    assert len(findings) == 1
    assert findings[0]["name"] == "MyClass.do_work"
    assert findings[0]["kind"] == "return_documented_on_none"
