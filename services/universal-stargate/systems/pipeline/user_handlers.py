"""
User handlers directory loading.

Enables clients to deploy custom domain handlers via simple file copying,
without requiring package installation or entry points.

Discovery Pattern:
    {base}/consensus/handlers/           → Shared domain handlers (loaded first)
    {base}/consensus/v3/handlers/        → v3-specific handlers
    {base}/consensus/v4-analytical/handlers/ → v4-specific handlers

Loading Order:
    1. Shared handlers ({domain}/handlers/) - alphabetically by domain
    2. Variant handlers ({domain}/{variant}/handlers/) - alphabetically by variant

    Within each domain, shared loads before variants, enabling variants to
    override shared step_type registrations (last registration wins).

Package Semantics:
    Each handlers/ directory is loaded as an isolated package with its own
    namespace. Relative imports work correctly:
        from .combine import ConsensusCombineHandler  # Works
        from .synthesis import MyHandler               # Works

    No sys.path mutation - modules are isolated by unique package name.

Example Structure:
    pipelines.local/
    └── consensus/
        ├── handlers/               # Shared (loaded first)
        │   ├── __init__.py        # register_handlers(router)
        │   ├── combine.py
        │   └── filter.py
        └── v4-analytical/
            └── handlers/          # Variant (loaded after shared)
                ├── __init__.py    # register_handlers(router)
                └── synthesis.py   # Can override synthesis step_type

Invariants:
    - ∀ handlers_dir with __init__.py: MUST have register_handlers(router)
    - ∀ handlers_dir without __init__.py: skip silently (not a handler package)
    - ∀ handlers_dir with __init__.py but no register_handlers: ERROR, skip package
    - ∀ import failure in handlers: ERROR, skip package (other packages still load)
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    from .core.domain_router import DomainRouter

logger = get_logger(__name__)


class HandlerLoadError(Exception):
    """Fatal error loading handler package. Stops startup."""

    pass


class _VariantScopedRouter:
    """Proxy that injects variant into all handler registrations.

    Shared handlers (domain/handlers/) use variant="".
    Variant handlers (domain/v6.0/handlers/) get the variant name injected
    transparently — register_handlers() signature stays unchanged.
    """

    _inner: DomainRouter
    _variant: str

    def __init__(self, inner: DomainRouter, variant: str) -> None:
        self._inner = inner
        self._variant = variant

    def register_domain_handler_class(
        self,
        domain: str,
        step_type: str,
        handler_class: type,
        *,
        external: bool = False,
    ) -> None:
        self._inner.register_domain_handler_class(
            domain,
            step_type,
            handler_class,
            variant=self._variant,
            external=external,
        )


def load_user_handlers(config_base_dir: Path | None = None) -> int:
    """
    Load handlers from {domain}/handlers/ AND {domain}/{variant}/handlers/.

    Args:
        config_base_dir: Search path directory (e.g., "pipelines.local")

    Returns:
        Number of handler modules successfully loaded
    """
    if config_base_dir is None:
        logger.debug("No config_base_dir provided, skipping handler loading")
        return 0

    pipelines_dir = Path(config_base_dir).expanduser().resolve()

    if not pipelines_dir.exists():
        logger.debug(f"Pipelines directory not found: {pipelines_dir}")
        return 0

    from .core.domain_router import get_domain_router

    router = get_domain_router()
    loaded_count = 0

    for domain_dir in sorted(pipelines_dir.iterdir()):
        if not domain_dir.is_dir() or domain_dir.name.startswith("."):
            continue

        # Load shared handlers first (enables variant override)
        shared_handlers = domain_dir / "handlers"
        if shared_handlers.is_dir():
            try:
                loaded_count += _load_handlers_package(
                    handlers_dir=shared_handlers,
                    package_name=_make_package_name(domain_dir.name),
                    display_path=f"{domain_dir.name}/handlers",
                    router=router,
                )
            except HandlerLoadError as e:
                logger.error(
                    "⚠ Skipping domain %s (shared handlers failed): %s",
                    domain_dir.name,
                    e,
                )
                continue  # variants likely depend on shared, skip entire domain

        # Load variant-specific handlers (alphabetical order)
        # Each variant gets a scoped router that tags registrations
        # with the variant name, enabling isolated dispatch.
        for variant_dir in sorted(domain_dir.iterdir()):
            if not variant_dir.is_dir():
                continue
            if variant_dir.name.startswith("."):
                continue
            if variant_dir.name == "handlers":
                continue  # Already loaded above
            if variant_dir.name == "__pycache__":
                continue

            variant_handlers = variant_dir / "handlers"
            if variant_handlers.is_dir():
                scoped_router = _VariantScopedRouter(router, variant_dir.name)
                try:
                    loaded_count += _load_handlers_package(
                        handlers_dir=variant_handlers,
                        package_name=_make_package_name(
                            domain_dir.name, variant_dir.name
                        ),
                        display_path=(f"{domain_dir.name}/{variant_dir.name}/handlers"),
                        router=scoped_router,
                    )
                except HandlerLoadError as e:
                    logger.error(
                        "⚠ Skipping handler package %s/%s: %s",
                        domain_dir.name,
                        variant_dir.name,
                        e,
                    )

    if loaded_count > 0:
        logger.info(f"Loaded {loaded_count} handler package(s)")

    return loaded_count


def _make_package_name(*parts: str) -> str:
    """
    Create valid Python package name from path components.

    Sanitizes: replaces `-` and other non-identifier chars with `_`.

    Examples:
        ("consensus",) → "_pipeline_handlers_consensus"
        ("consensus", "v4-analytical") → "_pipeline_handlers_consensus_v4_analytical"
    """
    sanitized = "_".join(re.sub(r"[^a-zA-Z0-9]", "_", part) for part in parts)
    return f"_pipeline_handlers_{sanitized}"


def _load_handlers_package(
    handlers_dir: Path,
    package_name: str,
    display_path: str,
    router: DomainRouter | _VariantScopedRouter,
) -> int:
    """
    Load a handlers directory as an isolated Python package.

    Uses submodule_search_locations to enable relative imports without
    polluting sys.path.

    Args:
        handlers_dir: Path to handlers/__init__.py directory
        package_name: Unique module name (e.g., "_pipeline_handlers_consensus")
        display_path: Human-readable path for logs
        router: DomainRouter instance for registration

    Returns:
        1 if loaded successfully

    Raises:
        HandlerLoadError: If package is malformed or import fails
    """
    init_file = handlers_dir / "__init__.py"

    if not init_file.exists():
        # No __init__.py = not a handler package, skip silently
        return 0

    # Package-style loading with submodule_search_locations.
    # Include parent dir so variant handlers can import shared module (e.g. v4.0/types).
    spec = importlib.util.spec_from_file_location(
        package_name,
        init_file,
        submodule_search_locations=[str(handlers_dir), str(handlers_dir.parent)],
    )

    if spec is None or spec.loader is None:
        raise HandlerLoadError(
            f"Failed to create module spec for {handlers_dir}. "
            f"Check that __init__.py is valid Python."
        )

    module = importlib.util.module_from_spec(spec)
    sys.modules[package_name] = module

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        # Remove from sys.modules on failure
        sys.modules.pop(package_name, None)
        raise HandlerLoadError(
            f"Failed to import handlers from {display_path}: {e}"
        ) from e

    if not hasattr(module, "register_handlers"):
        # Remove from sys.modules on validation failure (same as import failure)
        sys.modules.pop(package_name, None)
        raise HandlerLoadError(
            f"Handler package {display_path}/__init__.py is missing "
            f"register_handlers(router). Every handlers/__init__.py MUST define: "
            f"def register_handlers(router): ..."
        )

    try:
        module.register_handlers(router)
    except Exception as e:
        raise HandlerLoadError(
            f"register_handlers() failed in {display_path}: {e}"
        ) from e

    logger.info(f"✅ Loaded handlers: {display_path}")
    return 1
