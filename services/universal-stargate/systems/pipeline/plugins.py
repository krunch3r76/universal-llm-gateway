"""
Plugin API for external domain handlers.

External projects register handlers via:
1. Entry points (automatic discovery at startup)
2. Direct registration (explicit, for testing or simple cases)

Invariant: ∀ external_handler: implements(StepHandler) ∧ has_step_type

Usage (entry points - recommended):
    # In external project's pyproject.toml:
    [project.entry-points."stargate.domains"]
    ocr = "weekley.pipeline:register_handlers"

    # In weekley/pipeline/__init__.py:
    def register_handlers(router):
        router.register_domain_handler_class("ocr", "detect_issues", DetectHandler)

Usage (direct registration - for testing):
    from universal_stargate.systems.pipeline.plugins import register_domain_handler
    register_domain_handler("ocr", "detect_issues", DetectHandler)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

_discovery_complete: bool = False


def register_domain_handler(
    domain: str,
    step_type: str,
    handler_class: type,
) -> None:
    """
    Register an external domain handler.

    Called by external projects to register their handlers:

        from universal_stargate.systems.pipeline.plugins import register_domain_handler
        register_domain_handler("ocr", "detect_issues", DetectIssuesHandler)

    Args:
        domain: Pipeline type (e.g., "ocr", "code_review")
        step_type: Step type (e.g., "detect_issues", "proofread")
        handler_class: Handler class with step_type attribute

    Raises:
        ValueError: If handler_class lacks step_type attribute
    """
    if not hasattr(handler_class, "step_type"):
        raise ValueError(
            f"Handler class {handler_class.__name__} must have 'step_type' attribute"
        )

    from .core.domain_router import get_domain_router

    router = get_domain_router()
    # Mark as external for diagnostics
    router.register_domain_handler_class(
        domain, step_type, handler_class, external=True
    )
    logger.info(f"Registered external handler: {domain}.{step_type}")


def register_domain(
    domain: str,
    handlers: dict[str, type],
) -> None:
    """
    Register multiple handlers for a domain at once.

    Convenience function for registering all handlers in a domain:

        register_domain("ocr", {
            "detect_issues": DetectIssuesHandler,
            "preprocess": PreprocessHandler,
            "proofread": ProofreadHandler,
        })

    Args:
        domain: Pipeline type
        handlers: Mapping of step_type -> handler_class
    """
    for step_type, handler_class in handlers.items():
        register_domain_handler(domain, step_type, handler_class)


def discover_plugins() -> None:
    """
    Discover and load plugins from entry points.

    External packages declare entry points in pyproject.toml:

        [project.entry-points."stargate.domains"]
        ocr = "weekley.pipeline:register_handlers"

    The registered function receives the DomainRouter and should
    call router.register_domain_handler_class() for each handler.

    Called automatically during DomainRouter initialization.
    """
    global _discovery_complete

    if _discovery_complete:
        return

    from importlib.metadata import entry_points

    from .core.domain_router import get_domain_router

    try:
        # Python 3.12+ API
        eps = entry_points(group="stargate.domains")
    except TypeError:
        # Python 3.9 compatibility (not needed but harmless)
        all_eps = entry_points()
        eps = all_eps.get("stargate.domains", [])

    router = get_domain_router()
    loaded_count = 0

    for ep in eps:
        try:
            register_func = ep.load()
            register_func(router)
            loaded_count += 1
            logger.info(f"Loaded plugin domain: {ep.name}")
        except Exception as e:
            logger.warning(f"Failed to load plugin {ep.name}: {e}")

    if loaded_count > 0:
        logger.info(f"Discovered {loaded_count} external domain plugin(s)")

    _discovery_complete = True


def reset_discovery() -> None:
    """Reset discovery state (for testing only)."""
    global _discovery_complete
    _discovery_complete = False
