"""Omitted-intent guard for production entity_get calls and hint strings.

Match grammar from the ratified sparse-default spec:
(i) an AST call whose first positional or tool/op keyword is the literal
``entity_get`` and whose payload lacks ``intent``;
(ii) a string literal containing ``entity_get``, then ``{`` and ``entity_id``,
without ``intent``.
A payload that cannot be resolved statically is a violation unless the site
is named in ``_ALLOWLIST`` as ``path`` + source anchor + reason.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from scripts.cortex._skill_git_guard import PERSONAL_SKILL_SLUGS

_ENTITY_ID_KEY = re.compile(r"""['\"]entity_id['\"]\s*:""")

_REPO_ROOT = Path(__file__).resolve().parents[2]

# path, source anchor that must appear on the violation, reason
_ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    (
        "libs/web_chat_relay/orion_evidence_card.py",
        'execute_op("entity_get", kwargs)',
        "wrapper forwards the caller payload; the production caller passes intent=card",
    ),
    (
        "libs/implement_admission/conductor_witness_defaults.py",
        '_cortex_dispatch("entity_get", arguments)',
        "arguments dict sets intent=preview before caller kwargs override it",
    ),
    (
        "libs/cortex_store/dispatch_ops/_entity_get_param_surface.py",
        'op="entity_get"',
        "warning catalog names the op; it does not read an entity",
    ),
    (
        "libs/cortex_store/routes/entities.py",
        'x_mcp("entity_get")',
        "route registration, not an entity read",
    ),
    (
        "services/git_integration_worker/relay/tier_m_manifest.py",
        'op="entity_get"',
        "manifest row describes the op; it does not read an entity",
    ),
)

_NON_PY_ROOTS = (
    "docs",
    "cursor-plugins",
    ".cursor",
    "agent-surface",
    "scripts",
)

_NON_PY_SUFFIXES = {".md", ".mdc", ".txt", ".yml", ".yaml"}

_PREVIEW_SOURCE_URI_ONLY = frozenset({"source_uri"})


def _production_py_files() -> list[Path]:
    out: list[Path] = []
    for name in ("libs", "services"):
        root = _REPO_ROOT / name
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "tmp" in path.parts:
                continue
            if path.name.startswith("test_") or path.name == "conftest.py":
                continue
            out.append(path)
    return out


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                parts.append("")
            else:
                return None
        return "".join(parts)
    return None


def _omits_intent_text(text: str) -> bool:
    start = 0
    while True:
        idx = text.find("entity_get", start)
        if idx < 0:
            return False
        window = text[idx:]
        if "{" in window and _ENTITY_ID_KEY.search(window) and "intent" not in window:
            return True
        start = idx + len("entity_get")


def _call_is_entity_get(node: ast.Call) -> bool:
    if node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value == "entity_get":
            return True
    for kw in node.keywords:
        if kw.arg in {"tool", "op"} and isinstance(kw.value, ast.Constant):
            if kw.value.value == "entity_get":
                return True
    return False


def _payload_has_intent(call: ast.Call) -> bool | None:
    """True when intent is present, False when omitted, None when unresolved."""
    if any(kw.arg == "intent" for kw in call.keywords):
        return True
    payload: ast.AST | None = call.args[1] if len(call.args) >= 2 else None
    if payload is None:
        for kw in call.keywords:
            if kw.arg in {"arguments", "payload", "params"}:
                payload = kw.value
                break
    if payload is None:
        return None
    if isinstance(payload, ast.Dict):
        for key in payload.keys:
            if isinstance(key, ast.Constant) and key.value == "intent":
                return True
        return False
    text = _literal_text(payload)
    if text is None:
        if isinstance(payload, ast.Call):
            rendered = ast.unparse(payload)
            if "intent" in rendered:
                return True
            return None
        return None
    return "intent" in text


def _allowed(rel: str, segment: str) -> bool:
    return any(
        path == rel and anchor in segment for path, anchor, _reason in _ALLOWLIST
    )


def _enclosing_function(
    node: ast.AST, parents: dict[ast.AST, ast.AST]
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    current: ast.AST | None = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
    return None


def _get_accessed_keys(
    root: ast.AST, bound_names: frozenset[str]
) -> set[str] | None:
    """Return ``.get('key')`` string keys read from ``bound_names``, or None if unknown."""
    keys: set[str] = set()
    for child in ast.walk(root):
        if not isinstance(child, ast.Call):
            continue
        if not isinstance(child.func, ast.Attribute) or child.func.attr != "get":
            continue
        base = child.func.value
        if isinstance(base, ast.Name) and base.id in bound_names:
            if not child.args:
                return None
            key_node = child.args[0]
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                keys.add(key_node.value)
            else:
                return None
        if base == root and child.args:
            key_node = child.args[0]
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                keys.add(key_node.value)
            else:
                return None
    return keys


def _preview_sufficient_entity_get(
    call: ast.Call,
    parents: dict[ast.AST, ast.AST],
) -> bool:
    """Call omits intent but only reads ``source_uri`` from the preview default."""
    parent = parents.get(call)
    if parent is None:
        return False
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
        if parent.func.attr == "get" and parent.func.value == call:
            keys = _get_accessed_keys(parent, frozenset())
            return keys == _PREVIEW_SOURCE_URI_ONLY if keys is not None else False
    bound: set[str] = set()
    if isinstance(parent, ast.Assign):
        for target in parent.targets:
            if isinstance(target, ast.Name):
                bound.add(target.id)
    if not bound:
        return False
    func = _enclosing_function(call, parents)
    if func is None:
        return False
    keys: set[str] = set()
    for child in ast.walk(func):
        if not isinstance(child, ast.Call):
            continue
        if not isinstance(child.func, ast.Attribute) or child.func.attr != "get":
            continue
        if isinstance(child.func.value, ast.Name) and child.func.value.id in bound:
            accessed = _get_accessed_keys(child, bound)
            if accessed is None:
                return False
            keys |= accessed
    return bool(keys) and keys <= _PREVIEW_SOURCE_URI_ONLY


def _violations_in_py(path: Path) -> list[str]:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"{path}: syntax error {exc}"]
    rel = str(path.relative_to(_REPO_ROOT))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    def _inside_joined(node: ast.AST) -> bool:
        current: ast.AST | None = node
        while current in parents:
            current = parents[current]
            if isinstance(current, ast.JoinedStr):
                return True
        return False

    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_is_entity_get(node):
            present = _payload_has_intent(node)
            if present is True:
                continue
            if present is False and _preview_sufficient_entity_get(node, parents):
                continue
            segment = ast.get_source_segment(source, node) or ""
            if _allowed(rel, segment):
                continue
            found.append(f"{rel}:{node.lineno}: call omits intent ({segment[:180]!r})")
        text = (
            None
            if isinstance(node, ast.Call) or _inside_joined(node)
            else _literal_text(node)
        )
        if isinstance(node, ast.JoinedStr):
            text = _literal_text(node)
        if text and _omits_intent_text(text):
            segment = ast.get_source_segment(source, node) or text
            if _allowed(rel, segment):
                continue
            lineno = getattr(node, "lineno", 0)
            found.append(f"{rel}:{lineno}: hint omits intent")
    return found


def _compliant_py_sites() -> list[str]:
    hits: list[str] = []
    for path in _production_py_files():
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        rel = str(path.relative_to(_REPO_ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_is_entity_get(node):
                if _payload_has_intent(node) is True:
                    hits.append(f"{rel}:{node.lineno}")
            text = _literal_text(node)
            if (
                text
                and "entity_get" in text
                and "intent" in text
                and "entity_id" in text
            ):
                hits.append(f"{rel}:{getattr(node, 'lineno', 0)}:hint")
    return hits


def _non_py_violations() -> list[str]:
    found: list[str] = []
    for name in _NON_PY_ROOTS:
        root = _REPO_ROOT / name
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in _NON_PY_SUFFIXES:
                continue
            if "tmp" in path.parts:
                continue
            if (
                ".cursor" in path.parts
                and "skills" in path.parts
                and path.parent.name in PERSONAL_SKILL_SLUGS
            ):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                continue
            if "entity_get" not in text:
                continue
            rel = str(path.relative_to(_REPO_ROOT))
            start = 0
            while True:
                idx = text.find("entity_get", start)
                if idx < 0:
                    break
                window = text[idx : idx + 600]
                line = text.count("\n", 0, idx) + 1
                anchor = window[:160]
                brace = window.find("{")
                if brace < 0:
                    start = idx + len("entity_get")
                    continue
                if re.search(r"""tool\s*=\s*['\"](?!entity_get\b)""", window[:brace]):
                    start = idx + len("entity_get")
                    continue
                if _ENTITY_ID_KEY.search(window) and "intent" not in window:
                    if not _allowed(rel, anchor) and not _allowed(rel, window):
                        found.append(f"{rel}:{line}: prose omits intent")
                start = idx + len("entity_get")
    return found


@pytest.mark.offline
def test_production_entity_get_sites_pass_intent() -> None:
    violations: list[str] = []
    for path in _production_py_files():
        violations.extend(_violations_in_py(path))
    violations.extend(_non_py_violations())
    assert not violations, "omitted intent:\n" + "\n".join(violations[:80])


@pytest.mark.offline
def test_intent_guard_is_non_vacuous() -> None:
    compliant = _compliant_py_sites()
    assert any("workflow_hints.py" in hit for hit in compliant), compliant[:20]
    assert any("prompts.py" in hit for hit in compliant)


@pytest.mark.offline
def test_allowlist_anchors_are_real() -> None:
    for rel, anchor, reason in _ALLOWLIST:
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert anchor in text, rel
        assert reason.strip()
