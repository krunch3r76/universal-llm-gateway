"""Production registry loader: rag-search handlers and lazy imports resolve."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from systems.pipeline.core.handlers.registry import HandlerRegistry
from systems.pipeline.registry.core import PipelineRegistry
from systems.pipeline.user_handlers import load_user_handlers

pytestmark = pytest.mark.offline

REPO = Path(__file__).resolve().parents[3]
V1_ROOT = Path(__file__).resolve().parent
_LOADER_PKG = "_pipeline_handlers_rag_search_v1"


def _load_rag_search_via_production_path() -> PipelineRegistry:
    HandlerRegistry._ensure_initialized()
    load_user_handlers(REPO / "pipelines")
    reg = PipelineRegistry(
        search_paths=[str(REPO / "pipelines")],
        config_base_dir=REPO,
    )
    reg.load()
    assert "rag-search" in reg.pipelines
    pipeline = reg.pipelines["rag-search"]
    assert pipeline.source_variant == "v1"
    return reg


def _loader_module_name_for_file(py_file: Path) -> str:
    rel = py_file.relative_to(V1_ROOT)
    parts = list(rel.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if parts and parts[0] == "handlers":
        parts = parts[1:]
    if not parts:
        return _LOADER_PKG
    return f"{_LOADER_PKG}.{'.'.join(parts)}"


def _resolve_relative_import(from_module: str, node: ast.ImportFrom) -> str | None:
    if node.level <= 0:
        return None
    parts = from_module.split(".")
    pkg_parts = parts[:-1] if len(parts) > 1 else parts
    up = node.level - 1
    if up > len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - up]
    tail = node.module
    if tail:
        return ".".join([*base, *tail.split(".")]) if base else tail
    return ".".join(base) if base else None


class _RelativeImportFromCollector(ast.NodeVisitor):
    """Collect relative ImportFrom nodes, skipping TYPE_CHECKING blocks."""

    def __init__(self) -> None:
        self.nodes: list[ast.ImportFrom] = []
        self._in_type_checking = False

    def visit_If(self, node: ast.If) -> None:
        is_type_checking = isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING"
        if is_type_checking:
            prev = self._in_type_checking
            self._in_type_checking = True
            for child in node.body:
                self.visit(child)
            self._in_type_checking = prev
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._in_type_checking:
            return
        if node.level and node.level > 0:
            self.nodes.append(node)


def _iter_v1_relative_imports() -> list[tuple[Path, ast.ImportFrom, str]]:
    found: list[tuple[Path, ast.ImportFrom, str]] = []
    for py_file in sorted(V1_ROOT.rglob("*.py")):
        if py_file.name.startswith("test_"):
            continue
        if "__pycache__" in py_file.parts:
            continue
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        from_module = _loader_module_name_for_file(py_file)
        collector = _RelativeImportFromCollector()
        collector.visit(tree)
        for node in collector.nodes:
            resolved = _resolve_relative_import(from_module, node)
            if resolved is None:
                line = node.lineno
                raise AssertionError(
                    f"{py_file.relative_to(V1_ROOT)}:{line}: "
                    f"relative import level {node.level} beyond loader package"
                )
            found.append((py_file, node, resolved))
    return found


def test_rag_search_step_handler_classes_importable() -> None:
    """Breaks when handlers/__init__.py or a sibling module is missing under v1/."""
    reg = _load_rag_search_via_production_path()
    pipeline = reg.pipelines["rag-search"]
    for step in pipeline.steps:
        enabled = step.get_domain_field("enabled", True)
        allow_disable = step.get_domain_field("allow_disable", False)
        if not enabled and not allow_disable:
            continue
        HandlerRegistry.get_class_or_raise(
            pipeline.type,
            step.type,
            variant=pipeline.source_variant,
        )


def test_rag_search_v1_relative_imports_resolve_via_loader_package() -> None:
    """Breaks when a v1 relative import has no module under the production loader pkg."""
    _load_rag_search_via_production_path()
    seen_modules: set[str] = set()
    for py_file, node, resolved in _iter_v1_relative_imports():
        if not resolved.startswith(_LOADER_PKG):
            rel = py_file.relative_to(V1_ROOT)
            raise AssertionError(
                f"{rel}:{node.lineno}: resolved {resolved!r} outside {_LOADER_PKG}"
            )
        if resolved in seen_modules:
            continue
        seen_modules.add(resolved)
        try:
            mod = importlib.import_module(resolved)
        except ModuleNotFoundError as exc:
            rel = py_file.relative_to(V1_ROOT)
            raise AssertionError(
                f"{rel}:{node.lineno}: from {'.' * node.level}{node.module or ''} "
                f"does not resolve under {_LOADER_PKG} ({exc})"
            ) from exc
        for alias in node.names:
            if alias.name == "*":
                continue
            assert hasattr(mod, alias.name), (
                f"{py_file.relative_to(V1_ROOT)}:{node.lineno}: "
                f"{resolved}.{alias.name} missing after loader import"
            )


def test_rag_search_lazy_term_expansion_import() -> None:
    """Breaks when term_expansion.py is absent (retrieve step pool B facet path)."""
    _load_rag_search_via_production_path()
    retrieval = importlib.import_module(f"{_LOADER_PKG}.retrieval_execution")
    facets = retrieval.compute_facets_from_text(
        "pipeline retrieve step pool B facet expansion",
        max_idf_terms=0,
    )
    assert isinstance(facets, list)
