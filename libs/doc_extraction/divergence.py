"""
Body-free signature<->docstring divergence detection.

Pure functions over the inventory's signature + docstring strings — no bodies, no
pipeline, no async. Detects the slice of "the docstring is wrong" that is provable
without reading function bodies:
  - a parameter documented in the docstring is absent from the signature
  - a return value is documented but the signature is `-> None`
  - documented param count drifts from the signature param count

Consumers: doc_generate enforce step, scripts/docstring-quality.
"""

from __future__ import annotations

import ast
import re
from typing import Any

# Google-style section headers.
_GOOGLE_ARGS_RE = re.compile(r"^\s*(?:Args|Arguments|Parameters)\s*:\s*$", re.MULTILINE)
_GOOGLE_RETURNS_RE = re.compile(r"^\s*(?:Returns|Yields)\s*:\s*$", re.MULTILINE)
# A Google arg line: "    name (type): desc"  or  "    name: desc"
_GOOGLE_ARG_LINE_RE = re.compile(r"^\s+(\w+)\s*(?:\([^)]*\))?\s*:", re.MULTILINE)
# reST field forms.
_REST_PARAM_RE = re.compile(r":param\s+(?:\S+\s+)?(\w+)\s*:", re.MULTILINE)
_REST_RETURNS_RE = re.compile(r":returns?\s*:", re.MULTILINE)

_SELFISH = {"self", "cls"}


def _section_body(docstring: str, header_re: re.Pattern[str]) -> str:
    """Return text from a Google section header until the next blank-line gap/header."""
    m = header_re.search(docstring)
    if m is None:
        return ""
    tail = docstring[m.end() :]
    # Stop at the next section header (a line ending in ':') or a blank line gap.
    stop = re.search(r"\n\s*\n|\n\s*\w[\w ]*:\s*\n", tail)
    return tail[: stop.start()] if stop else tail


def _documented_params(docstring: str) -> set[str]:
    params: set[str] = set(_REST_PARAM_RE.findall(docstring))
    args_body = _section_body(docstring, _GOOGLE_ARGS_RE)
    params.update(_GOOGLE_ARG_LINE_RE.findall(args_body))
    return params - _SELFISH


def _documents_return(docstring: str) -> bool:
    if _REST_RETURNS_RE.search(docstring):
        return True
    return bool(_GOOGLE_RETURNS_RE.search(docstring))


def _signature_facts(signature: str) -> tuple[set[str], str | None]:
    """Return (param names, return annotation source or None) from a signature string.

    `signature` is the inventory form, e.g. ``def foo(a: int, b: str = 3) -> None``
    (trailing colon already stripped upstream). Parsed via a synthesized def body.
    """
    src = signature.strip()
    if not src.startswith(("def ", "async def ", "class ")):
        return set(), None
    if src.startswith("class "):
        return set(), None
    try:
        module = ast.parse(src + ": ...")
    except SyntaxError:
        return set(), None
    fn = module.body[0]
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return set(), None
    args = fn.args
    names: set[str] = set()
    for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs):
        names.add(arg.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    names -= _SELFISH
    ret = ast.unparse(fn.returns) if fn.returns is not None else None
    return names, ret


def detect_symbol_divergence(
    *, path: str, name: str, signature: str, docstring: str, line: int
) -> list[dict[str, Any]]:
    """Return divergence findings for one symbol (empty if none / no docstring)."""
    if not docstring.strip():
        return []
    findings: list[dict[str, Any]] = []
    sig_params, ret_ann = _signature_facts(signature)
    doc_params = _documented_params(docstring)

    phantom = sorted(doc_params - sig_params)
    for param in phantom:
        findings.append(
            {
                "path": path,
                "line": line,
                "name": name,
                "kind": "param_absent_from_signature",
                "detail": f"documented param '{param}' not in signature",
            }
        )
    if _documents_return(docstring) and ret_ann == "None":
        findings.append(
            {
                "path": path,
                "line": line,
                "name": name,
                "kind": "return_documented_on_none",
                "detail": "docstring documents a return but signature is -> None",
            }
        )
    return findings


def detect_inventory_divergence(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Scan an extracted inventory for signature<->docstring divergence."""
    findings: list[dict[str, Any]] = []
    for fn in inventory.get("functions", []):
        findings.extend(
            detect_symbol_divergence(
                path=str(fn.get("path", "")),
                name=str(fn.get("name", "")),
                signature=str(fn.get("signature", "")),
                docstring=str(fn.get("docstring", "")),
                line=int(fn.get("line") or 1),
            )
        )
    for cls in inventory.get("classes", []):
        class_name = str(cls.get("name", ""))
        for method in cls.get("methods", []):
            findings.extend(
                detect_symbol_divergence(
                    path=str(cls.get("path", "")),
                    name=f"{class_name}.{method.get('name', '')}",
                    signature=str(method.get("signature", "")),
                    docstring=str(method.get("docstring", "")),
                    line=int(method.get("line") or cls.get("line") or 1),
                )
            )
    return findings


_BEHAVIORAL_RE = re.compile(
    r"\b(raises?|never|always|invariant|guarantee)\b", re.IGNORECASE
)


def behavioral_claim_symbols(inventory: dict[str, Any]) -> list[dict[str, Any]]:
    """Symbols whose docstrings make behavioral claims, with bodies for verification.

    Requires the inventory to have been extracted with include_bodies=True; symbols
    lacking body_source are skipped (no verification target).
    """
    out: list[dict[str, Any]] = []
    for fn in inventory.get("functions", []):
        doc = str(fn.get("docstring", ""))
        body = fn.get("body_source")
        if doc and body and _BEHAVIORAL_RE.search(doc):
            out.append(
                {
                    "name": str(fn.get("name", "")),
                    "path": str(fn.get("path", "")),
                    "docstring": doc,
                    "body_source": str(body),
                }
            )
    for cls in inventory.get("classes", []):
        for method in cls.get("methods", []):
            doc = str(method.get("docstring", ""))
            body = method.get("body_source")
            if doc and body and _BEHAVIORAL_RE.search(doc):
                out.append(
                    {
                        "name": f"{cls.get('name', '')}.{method.get('name', '')}",
                        "path": str(cls.get("path", "")),
                        "docstring": doc,
                        "body_source": str(body),
                    }
                )
    return out
