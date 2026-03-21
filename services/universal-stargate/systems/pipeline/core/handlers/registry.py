"""
Step handler registry integrated with domain routing.

Resolution order:
1. Domain-specific handler via DomainRouter
2. Generic handler registered directly
3. KeyError if not found (fail-fast)

Invariant: ∀ (domain, step_type), ∃! resolved handler
"""
# ruff: noqa: E501

from __future__ import annotations

from typing import TYPE_CHECKING

from universal_logging import get_logger

from ..domain_router import get_domain_router

if TYPE_CHECKING:
    from ..schemas import StepConfig
    from .protocol import PipelineContext, StepHandler, StepOutput

logger = get_logger(__name__)


class HandlerRegistry:
    """
    Registry of step handler CLASSES (not instances).

    DESIGN: Registers handler classes and instantiates per-execution.
    This avoids shared mutable state between requests and enables
    proper dependency injection.

    Handlers are instantiated fresh for each step execution, with
    dependencies injected via the PipelineContext.

    Delegates to DomainRouter for domain-aware resolution.
    """

    # Maps step_type -> handler CLASS (not instance)
    _generic_handler_classes: dict[str, type[StepHandler]] = {}
    _initialized: bool = False

    @classmethod
    def register_class(cls, handler_class: type[StepHandler]) -> type[StepHandler]:
        """
        Register a generic (fallback) step handler class.

        Args:
            handler_class: Handler class to register (not instance!)

        Returns:
            The handler class (useful for direct registration or internal chaining).
        """
        step_type = handler_class.step_type
        if step_type in cls._generic_handler_classes:
            logger.warning(f"Overwriting generic handler for '{step_type}'")
        cls._generic_handler_classes[step_type] = handler_class

        # Also register with domain router as generic
        router = get_domain_router()
        router.register_generic_handler_class(step_type, handler_class)

        logger.debug(f"Registered generic handler class: {step_type}")
        return handler_class

    @classmethod
    def get_class(
        cls, domain: str, step_type: str, *, variant: str = ""
    ) -> type[StepHandler] | None:
        """
        Get handler CLASS for (domain, variant, step_type).

        Uses domain router for resolution with variant fallback.
        """
        cls._ensure_initialized()
        try:
            router = get_domain_router()
            return router.resolve_class(domain, step_type, variant=variant)
        except KeyError:
            return None

    @classmethod
    def get_class_or_raise(
        cls, domain: str, step_type: str, *, variant: str = ""
    ) -> type[StepHandler]:
        """Get handler class or raise KeyError (fail-fast)."""
        handler_class = cls.get_class(domain, step_type, variant=variant)
        if handler_class is None:
            raise KeyError(
                f"No handler for ({domain}, {variant!r}, {step_type}). "
                f"Available: {cls.list_handlers()}"
            )
        return handler_class

    @classmethod
    def create_handler(
        cls, domain: str, step_type: str, *, variant: str = ""
    ) -> StepHandler:
        """
        Create a fresh handler instance for step execution.

        Called once per step execution to ensure no shared state.
        """
        handler_class = cls.get_class_or_raise(domain, step_type, variant=variant)
        return handler_class()

    @classmethod
    async def execute(
        cls,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """
        Execute a step using a freshly instantiated handler.

        Args:
            step: Step specification
            context: Pipeline execution context

        Returns:
            StepOutput from handler

        NOTE: This returns StepOutput. The caller (DAGExecutor)
        is responsible for writing to context.outputs.
        """
        handler = cls.create_handler(
            context.domain, step.type, variant=context.pipeline.source_variant
        )
        return await handler.execute(step, context)

    @classmethod
    def validate_step(
        cls, domain: str, step: StepConfig, *, variant: str = ""
    ) -> list[str]:
        """Validate a step configuration using a temporary handler."""
        handler_class = cls.get_class(domain, step.type, variant=variant)
        if handler_class is None:
            return [f"Unknown step type: {step.type} for domain: {domain}"]
        handler = handler_class()
        return handler.validate(step)

    @classmethod
    def list_types(cls) -> list[str]:
        """List all registered generic step types."""
        # _ensure_initialized is called by other public methods that might precede this.
        # Keeping it for robustness, but noting it's idempotent.
        cls._ensure_initialized()
        return sorted(cls._generic_handler_classes.keys())

    @classmethod
    def list_handlers(cls) -> dict[str, list[str]]:
        """List all handlers by category."""
        cls._ensure_initialized()
        router = get_domain_router()
        return router.list_handlers()

    @classmethod
    def get_handler_dependency_fields(
        cls, step_type: str, domain: str = ""
    ) -> tuple[str, ...]:
        """Return domain-field names that contain step-name references.

        Used by StepConfig.computed_depends_on to discover implicit
        dependencies declared by handlers (e.g. select_output.candidates).

        When *domain* is empty the search falls back to scanning all
        registered domain handlers by step_type so that DAG dependency
        resolution works even without pipeline-level domain context.
        """
        cls._ensure_initialized()
        # Option 1: Require domain for this method, or clarify behavior for empty domain
        # For now, let's assume we want to query the router for a suitable handler.
        # This requires a new public method on DomainRouter.
        router = get_domain_router()
        if domain:
            handler_class = cls.get_class(domain, step_type, variant="")
        else:
            # This assumes DomainRouter has a public method to find a handler class
            # by step_type across domains, or a clear strategy for 'generic' lookup.
            # For now, we'll call a hypothetical method.
            handler_class = router.find_handler_class_by_step_type(step_type)

        if handler_class is None:
            return ()
        return getattr(handler_class, "dependency_fields", ())

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Ensure builtin and domain handlers are registered."""
        if cls._initialized:
            return

        # Import builtin handlers to trigger their module-level `register_handler` decorators,
        # which populate the registry.
        from . import builtin  # noqa: F401

        # Trigger domain handler loading via router
        router = get_domain_router()
        router._ensure_initialized()

        cls._initialized = True
        logger.info(
            f"Handler registry initialized: "
            f"{len(cls._generic_handler_classes)} generic handler classes"
        )


def register_handler(handler_class: type[StepHandler]) -> type[StepHandler]:
    """
    Decorator to register a generic step handler class.

    DESIGN: Registers the CLASS, not an instance. Handlers are
    instantiated per-execution to avoid shared mutable state.

    Usage:
        @register_handler
        class MyHandler:
            step_type = "my_step"
            async def execute(self, step, context) -> StepOutput:
                # Return StepOutput, do NOT write to context
                return StepOutput(raw="result")

    Handlers MUST be stateless or use per-execution state only.
    If a handler needs dependencies (registry, invoker), they
    are accessed via the context, not constructor.
    """
    HandlerRegistry.register_class(handler_class)
    return handler_class
