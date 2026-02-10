"""
Step handler registry integrated with domain routing.

Resolution order:
1. Domain-specific handler via DomainRouter
2. Generic handler registered directly
3. KeyError if not found (fail-fast)

Invariant: ∀ (domain, step_type), ∃! resolved handler
"""

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
    _generic_handler_classes: dict[str, type] = {}
    _initialized: bool = False

    @classmethod
    def register_class(cls, handler_class: type) -> type:
        """
        Register a generic (fallback) step handler class.

        Args:
            handler_class: Handler class to register (not instance!)

        Returns:
            The handler class (for decorator chaining)
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
    def get_class(cls, domain: str, step_type: str) -> type | None:
        """
        Get handler CLASS for (domain, step_type).

        Uses domain router for resolution.
        """
        cls._ensure_initialized()
        try:
            router = get_domain_router()
            return router.resolve_class(domain, step_type)
        except KeyError:
            return None

    @classmethod
    def get_class_or_raise(cls, domain: str, step_type: str) -> type:
        """Get handler class or raise KeyError (fail-fast)."""
        handler_class = cls.get_class(domain, step_type)
        if handler_class is None:
            raise KeyError(
                f"No handler for ({domain}, {step_type}). "
                f"Available: {cls.list_handlers()}"
            )
        return handler_class

    @classmethod
    def create_handler(cls, domain: str, step_type: str) -> StepHandler:
        """
        Create a fresh handler instance for step execution.

        Called once per step execution to ensure no shared state.
        """
        handler_class = cls.get_class_or_raise(domain, step_type)
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
        # Create fresh handler instance for this execution
        handler = cls.create_handler(context.domain, step.type)
        return await handler.execute(step, context)

    @classmethod
    def validate_step(cls, domain: str, step: StepConfig) -> list[str]:
        """Validate a step configuration using a temporary handler."""
        handler_class = cls.get_class(domain, step.type)
        if handler_class is None:
            return [f"Unknown step type: {step.type} for domain: {domain}"]
        # Create temporary instance for validation
        handler = handler_class()
        return handler.validate(step)

    @classmethod
    def list_types(cls) -> list[str]:
        """List all registered generic step types."""
        cls._ensure_initialized()
        return sorted(cls._generic_handler_classes.keys())

    @classmethod
    def list_handlers(cls) -> dict[str, list[str]]:
        """List all handlers by category."""
        cls._ensure_initialized()
        router = get_domain_router()
        return router.list_handlers()

    @classmethod
    def _ensure_initialized(cls) -> None:
        """Ensure builtin and domain handlers are registered."""
        if cls._initialized:
            return

        # Import builtin handlers to trigger registration
        from . import builtin  # noqa: F401

        # Trigger domain handler loading via router
        router = get_domain_router()
        router._ensure_initialized()

        cls._initialized = True
        logger.info(
            f"Handler registry initialized: "
            f"{len(cls._generic_handler_classes)} generic handler classes"
        )


def register_handler(handler_class: type) -> type:
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
