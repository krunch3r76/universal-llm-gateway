"""Handler package discovery and validation."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def discover_handler_packages(root_dir: Path) -> list[Path]:
    """
    Discover all handler packages under the given directory.

    Returns paths to handler directories that contain __init__.py.
    Handles both shared ({domain}/handlers/) and variant ({domain}/{variant}/handlers/)
    structures.

    Discovery logic:
    - If root_dir is a file: use parent directory as search root
    - rglob for handlers directories under search root
    - Check parent of search root for shared handlers (enables variant → shared discovery)
    - Check grandparent for sibling-domain handlers (finds generic handlers in other
      domains, e.g. pipelines/tools/handlers/ when validating pipelines/rag/project_journal_v2/)

    Loading order: sibling-domain (generic) first, then shared, then variant.

    Example for {domain}/{variant}/pipeline.yaml:
    - search_root = {domain}/{variant}/
    - rglob finds {domain}/{variant}/handlers/
    - parent check finds {domain}/handlers/ (shared)
    - grandparent scan finds {search_path_root}/*/handlers/ (generic handlers in
      sibling domains, e.g. tools/handlers/ with register_generic_handler_class)
    """
    handler_dirs = []

    # Determine search root (parent directory if given a file)
    search_root = root_dir if root_dir.is_dir() else root_dir.parent

    # Check for sibling-domain handlers at grandparent level.
    # For pipelines/rag/project_journal_v2/ → grandparent = pipelines/
    # Scans pipelines/*/handlers/ to discover generic handlers (e.g. tools/handlers/).
    grandparent = search_root.parent.parent
    if grandparent.is_dir():
        for sibling_domain in sorted(grandparent.iterdir()):
            if not sibling_domain.is_dir() or sibling_domain.name.startswith("."):
                continue
            # Skip the domain we're already scanning (handled below via rglob)
            if sibling_domain == search_root.parent:
                continue
            sibling_handlers = sibling_domain / "handlers"
            if (
                sibling_handlers.is_dir()
                and (sibling_handlers / "__init__.py").exists()
                and sibling_handlers not in handler_dirs
            ):
                handler_dirs.append(sibling_handlers)

    # Check for shared handlers in parent domain directory.
    # Variant structure: {domain}/{variant}/ needs {domain}/handlers/
    parent_handlers = search_root.parent / "handlers"
    if (
        parent_handlers.is_dir()
        and (parent_handlers / "__init__.py").exists()
        and parent_handlers not in handler_dirs
    ):
        handler_dirs.append(parent_handlers)  # Shared before variant

    # Direct discovery under search_root
    for handlers_dir in sorted(search_root.rglob("handlers")):
        if not handlers_dir.is_dir():
            continue
        init_file = handlers_dir / "__init__.py"
        if init_file.exists() and handlers_dir not in handler_dirs:
            handler_dirs.append(handlers_dir)

    return handler_dirs


def validate_handler_package(handler_dir: Path) -> tuple[bool, list[str], set[str]]:
    """
    Validate a handler package.

    Requirements:
    - __init__.py MUST exist (already checked before calling)
    - __init__.py MUST have register_handlers() function
    - Import must succeed without errors

    Returns:
        (is_valid, errors, registered_step_types)
    """
    errors = []
    step_types: set[str] = set()
    init_file = handler_dir / "__init__.py"

    # Check register_handlers exists using AST (no import needed)
    try:
        source = init_file.read_text()
        tree = ast.parse(source)
    except SyntaxError as e:
        return (False, [f"Syntax error in {init_file}: {e}"], set())

    has_register_handlers = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "register_handlers":
            has_register_handlers = True
            break

    if not has_register_handlers:
        errors.append(
            f"Handler package {handler_dir} missing register_handlers() function. "
            f"Policy: __init__.py present but no register_handlers() → FATAL"
        )
        return (False, errors, set())

    # Extract registered step types from register_handlers() body.
    # Handles two registration forms:
    #   router.register_domain_handler_class("domain", "step_type", Class)  → arg index 1
    #   router.register_generic_handler_class("step_type", Class)           → arg index 0
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "register_handlers":
            for stmt in ast.walk(node):
                if isinstance(stmt, ast.Call):
                    func = stmt.func
                    if not isinstance(func, ast.Attribute):
                        continue
                    if (
                        func.attr == "register_domain_handler_class"
                        and len(stmt.args) >= 2
                    ):
                        if isinstance(stmt.args[1], ast.Constant):
                            step_types.add(stmt.args[1].value)
                    elif (
                        func.attr == "register_generic_handler_class"
                        and len(stmt.args) >= 1
                    ):
                        if isinstance(stmt.args[0], ast.Constant):
                            step_types.add(stmt.args[0].value)

    return (True, errors, step_types)


def validate_all_handler_packages(
    root_dir: Path,
) -> tuple[bool, list[str], set[str]]:
    """
    Validate all handler packages under root_dir.

    Returns:
        (all_valid, all_errors, all_step_types)
    """
    all_errors = []
    all_step_types: set[str] = set()
    all_valid = True

    all_step_types.update(load_runtime_builtin_step_types())

    handler_dirs = discover_handler_packages(root_dir)

    for handler_dir in handler_dirs:
        valid, errors, step_types = validate_handler_package(handler_dir)
        if not valid:
            all_valid = False
        all_errors.extend(errors)
        all_step_types.update(step_types)

    return (all_valid, all_errors, all_step_types)


def load_runtime_builtin_step_types() -> set[str]:
    """Load generic handler step types from Stargate's runtime registry.

    The validation script statically scans pipeline-local `handlers/__init__.py`
    packages, which misses built-in generic handlers registered inside
    `services/universal-stargate/systems/pipeline/core/handlers/`. Import the
    runtime registry once so validation sees the same built-ins Stargate does.
    """

    project_root = Path(__file__).resolve().parents[2]
    stargate_path = project_root / "services" / "universal-stargate"

    if stargate_path.is_dir():
        stargate_path_str = str(stargate_path)
        if stargate_path_str not in sys.path:
            sys.path.insert(0, stargate_path_str)

    try:
        from systems.pipeline.core.handlers.registry import HandlerRegistry
    except ImportError:
        return set()

    return set(HandlerRegistry.list_types())
