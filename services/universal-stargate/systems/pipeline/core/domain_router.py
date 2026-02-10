"""
Domain routing for pipeline execution.

Routes (pipeline_type, step_type) to appropriate handler.

Resolution order:
1. Domain-specific handler: domains/{pipeline.type}/handlers.py
2. Generic handler: core/handlers/builtin.py
3. Error if not found (fail-fast)

Invariants:
- ∀ (domain, step_type): resolve() returns handler or raises
- Domain handlers take precedence over generic
"""

from __future__ import annotations

from universal_logging import get_logger

logger = get_logger(__name__)


class DomainRouter:
    """
    Route step execution to domain-specific or generic handlers.

    Handler resolution order:
    1. Domain-specific handler (domain, step_type)
    2. Generic handler (step_type)
    3. KeyError (fail-fast)

    Handler sources:
    - User handlers (from ~/.local/share/universal-stargate/handlers/)
    - External plugins (via entry points)
    - No built-in domains (pipeline system is fully domain-agnostic)

    Invariant: ∀ (domain, step_type): ∃! handler ∈ (domain ∪ generic)
    """

    def __init__(self):
        # (domain, step_type) -> handler CLASS
        self._domain_handler_classes: dict[tuple[str, str], type] = {}

        # step_type -> handler CLASS (fallback)
        self._generic_handler_classes: dict[str, type] = {}

        # Track external vs builtin for diagnostics
        self._external_domains: set[str] = set()

        self._initialized: bool = False

    def register_domain_handler_class(
        self,
        domain: str,
        step_type: str,
        handler_class: type,
        external: bool = False,
    ) -> None:
        """
        Register a domain-specific handler class.

        Override semantics: If step_type already registered for domain,
        the new handler replaces it (last registration wins). Logs at INFO.

        Args:
            domain: Pipeline type (e.g., "ocr", "translation")
            step_type: Step type (e.g., "generate", "detect_issues")
            handler_class: Handler class to register
            external: True if registered by external plugin
        """
        key = (domain, step_type)
        if key in self._domain_handler_classes:
            old_class = self._domain_handler_classes[key]
            logger.info(
                f"Handler override: {domain}/{step_type} "
                f"{old_class.__name__} → {handler_class.__name__}"
            )
        self._domain_handler_classes[key] = handler_class

        if external:
            self._external_domains.add(domain)

    def register_generic_handler_class(
        self,
        step_type: str,
        handler_class: type,
    ) -> None:
        """
        Register a generic (fallback) handler class.

        Example:
            router.register_generic_handler_class("generate", GenericGenerateHandler)
        """
        if step_type in self._generic_handler_classes:
            logger.warning(f"Overwriting generic handler class for '{step_type}'")
        self._generic_handler_classes[step_type] = handler_class

    def resolve_class(self, domain: str, step_type: str) -> type:
        """
        Resolve handler CLASS for (domain, step_type).

        Args:
            domain: Pipeline type (e.g., "translation", "code_review")
            step_type: Step type (e.g., "generate", "judge")

        Returns:
            Handler class (caller instantiates)

        Raises:
            KeyError: If no handler found (fail-fast)
        """
        self._ensure_initialized()

        # Try domain-specific first
        key = (domain, step_type)
        if key in self._domain_handler_classes:
            return self._domain_handler_classes[key]

        # Fall back to generic
        if step_type in self._generic_handler_classes:
            # logger.debug(f"Resolved generic handler class: {step_type}")
            return self._generic_handler_classes[step_type]

        # No handler found - fail fast
        available_domain = [
            f"{d}.{s}" for (d, s) in self._domain_handler_classes.keys()
        ]
        available_generic = list(self._generic_handler_classes.keys())

        msg = (
            f"No handler for ({domain}, {step_type}). "
            f"Domain handlers: {available_domain}, "
            f"Generic handlers: {available_generic}"
        )
        raise KeyError(msg)

    def is_external_domain(self, domain: str) -> bool:
        """Check if domain was registered by external plugin."""
        return domain in self._external_domains

    def list_handlers(self) -> dict[str, list[str]]:
        """
        List all registered handlers by category.

        Returns:
            Dict with 'domain', 'generic', and 'external' lists
        """
        self._ensure_initialized()

        domain_handlers = []
        external_handlers = []

        for domain, step_type in self._domain_handler_classes.keys():
            handler_str = f"{domain}.{step_type}"
            if domain in self._external_domains:
                external_handlers.append(handler_str)
            else:
                domain_handlers.append(handler_str)

        return {
            "domain": sorted(domain_handlers),
            "generic": sorted(self._generic_handler_classes.keys()),
            "external": sorted(external_handlers),
        }

    def _ensure_initialized(self) -> None:
        """
        Ensure domain handlers are loaded.

        Load order:
        1. Builtin domains (empty - no built-in domains)
        2. External plugins (via entry points)
        3. User handlers (via load_user_handlers() called before registry creation)

        All domain handlers are external - no built-in domains.
        Translation, consensus, etc. loaded from user handlers directory.
        """
        if self._initialized:
            return

        # 1. Import and register builtin domain handlers
        self._load_builtin_domains()

        # 2. Discover external plugins via entry points
        self._load_external_plugins()

        self._initialized = True

        handlers = self.list_handlers()
        logger.info(
            f"DomainRouter initialized: "
            f"{len(handlers['domain'])} builtin, "
            f"{len(handlers['external'])} external, "
            f"{len(handlers['generic'])} generic handlers"
        )

    def _load_builtin_domains(self) -> None:
        """
        Load builtin domain handlers.

        All domain handlers moved to user handlers directory.
        No built-in domains remain - pipeline system is fully domain-agnostic.
        """
        pass

    def _load_external_plugins(self) -> None:
        """Load external domain handlers via entry points."""
        from ..plugins import discover_plugins

        discover_plugins()


# Singleton
_domain_router: DomainRouter | None = None


def get_domain_router() -> DomainRouter:
    """Get or create domain router singleton."""
    global _domain_router
    if _domain_router is None:
        _domain_router = DomainRouter()
    return _domain_router
