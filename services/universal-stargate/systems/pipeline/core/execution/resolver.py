"""
Namespace resolution with pluggable handlers.

Separation of concerns:
- resolve(binding) → root object (NamespaceResolver)
- traverse_path(root, field_path) → final value (utility)

Extension: register_namespace() for custom namespaces
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from .errors import BindingResolutionError, InvalidNamespaceError

if TYPE_CHECKING:
    from ..schemas import InputBinding, SourceInput, StepOutput


class PipelineContextProtocol(Protocol):
    """Minimal interface for pipeline context required by resolver."""

    @property
    def source(self) -> "SourceInput":
        """Pipeline input data."""
        ...

    @property
    def options(self) -> dict[str, Any]:
        """Pipeline runtime options as dict."""
        ...

    @property
    def outputs(self) -> dict[str, "StepOutput"]:
        """Completed step outputs."""
        ...


class NamespaceHandler(Protocol):
    """Protocol for namespace-specific resolution."""

    def resolve(self, field_path: str) -> Any:
        """Resolve field_path within this namespace's root object."""
        ...


class AbstractNamespaceHandler(ABC):
    """
    Abstract base class for custom namespace handlers.

    Implement this class to add custom data sources accessible via pipeline
    bindings. For example, feature flags, external APIs, or cached data.

    Contract Requirements:
    ----------------------

    **Methods (Required):**

    - `resolve()` - Return root object for path traversal

    Invariants:
    -----------

    - ∀ resolve(path): returns object suitable for traverse_path()
    - resolve() is called once per binding resolution
    - Return value is traversed via traverse_path(result, binding.field_path)

    How It Works:
    -------------

    1. Pipeline YAML references: `handler_inputs: { flag: myNs.feature_enabled }`
    2. NamespaceResolver calls: `handler.resolve("feature_enabled")`
    3. Handler returns root object (e.g., dict, dataclass)
    4. traverse_path() navigates to final value

    Registration:
    -------------

    ```python
    resolver = NamespaceResolver(context)
    resolver.register_namespace("myNs", MyNamespaceHandler(service))
    ```

    Example:
    --------

    ```python
    @dataclass
    class FeatureFlagHandler(AbstractNamespaceHandler):
        '''Handler for featureNs.* bindings.'''

        _flag_service: FeatureFlagService

        def resolve(self, field_path: str) -> Any:
            '''Return flag service as root for traversal.'''
            # Return the service; traverse_path handles field_path
            return self._flag_service

    # In pipeline YAML:
    # handler_inputs:
    #   dark_mode: featureNs.dark_mode_enabled
    #   max_items: featureNs.config.max_items

    # Registration during pipeline init:
    resolver.register_namespace("featureNs", FeatureFlagHandler(flag_service))
    ```

    Built-in Handlers:
    ------------------

    - `SourceNamespaceHandler` - sourceNs.* (pipeline input)
    - `OptionsNamespaceHandler` - optionsNs.* (pipeline options)
    - `StepOutputNamespaceHandler` - {step_name}.* (step outputs)
    - `MapNamespaceHandler` - mapNs.* (map iteration context)

    Reserved Namespaces:
    --------------------

    Cannot override: sourceNs, optionsNs, loopNs, mapNs

    See Also:
    ---------
    - `NamespaceResolver` - Coordinates namespace resolution
    - `traverse_path()` - Navigates dot-separated paths
    - README.md "Namespace Quick Reference" for all namespaces
    """

    @abstractmethod
    def resolve(self, field_path: str) -> Any:
        """
        Resolve field_path to root object for traversal.

        Args:
            field_path: The path portion after namespace prefix.
                       For binding "myNs.config.enabled", this is "config.enabled"

        Returns:
            Root object that traverse_path() will navigate.
            Can be dict, dataclass, object with attributes, or primitive.

        Note:
            You typically ignore field_path and return your root object.
            The NamespaceResolver uses traverse_path() to navigate field_path.

            Exception: If you want to intercept specific paths, check field_path
            and return appropriate sub-object.

        Example:
            ```python
            def resolve(self, field_path: str) -> Any:
                # Simple: return root, let traverse_path handle navigation
                return self._config_dict

                # Advanced: intercept specific paths
                if field_path.startswith("secret."):
                    raise PermissionError("Cannot access secrets")
                return self._config_dict
            ```
        """
        ...


@dataclass
class SourceNamespaceHandler:
    """Handler for sourceNs.* (pipeline input)."""

    source: "SourceInput"

    def resolve(self, field_path: str) -> Any:
        """Return source object for traversal."""
        return self.source


@dataclass
class OptionsNamespaceHandler:
    """Handler for optionsNs.* (pipeline options)."""

    options: dict

    def resolve(self, field_path: str) -> Any:
        """Return options dict for traversal."""
        return self.options


@dataclass
class StepOutputNamespaceHandler:
    """Handler for step_name.* (previous step outputs)."""

    outputs: dict[str, "StepOutput"]

    def resolve_step(self, step_name: str) -> "StepOutput":
        """Return StepOutput for given step."""
        if step_name not in self.outputs:
            raise KeyError(f"Step '{step_name}' has not executed yet or doesn't exist")
        return self.outputs[step_name]


@dataclass
class MapNamespaceHandler:
    """
    Handler for mapNs.* (map iteration context).

    Only registered during map step execution.
    """

    state: Any  # MapState (avoid circular import)

    def resolve(self, field_path: str) -> Any:
        """Return MapState for traversal."""
        return self.state


class NamespaceResolver:
    """
    Centralized namespace resolution.

    Invariant: ∀ binding, resolve(binding) returns root object for traverse_path()
    """

    RESERVED_NAMESPACES = frozenset({"sourceNs", "optionsNs", "loopNs", "mapNs"})

    def __init__(self, context: PipelineContextProtocol):
        self._context = context
        self._handlers: dict[str, NamespaceHandler] = {
            "sourceNs": SourceNamespaceHandler(context.source),
            "optionsNs": OptionsNamespaceHandler(context.options),
        }
        self._step_handler = StepOutputNamespaceHandler(context.outputs)

    def with_map_context(self, map_state: Any) -> "NamespaceResolver":
        """
        Create resolver with map context for iteration.

        Returns new resolver with mapNs registered.
        Does NOT modify self (immutable pattern for async safety).

        Args:
            map_state: MapState instance for this iteration

        Returns:
            New resolver with mapNs handler registered
        """
        # Shallow copy handlers
        new_resolver = NamespaceResolver.__new__(NamespaceResolver)
        new_resolver._context = self._context
        new_resolver._handlers = {**self._handlers}
        new_resolver._handlers["mapNs"] = MapNamespaceHandler(map_state)
        new_resolver._step_handler = self._step_handler
        return new_resolver

    def resolve(self, binding: "InputBinding") -> Any:
        """
        Resolve binding to root object.

        Returns root object; caller uses traverse_path() for field navigation.
        """
        if binding.namespace in self._handlers:
            handler = self._handlers[binding.namespace]
            return handler.resolve(binding.field_path)

        if binding.namespace == "step":
            if binding.step_name is None:
                raise ValueError("Step namespace requires step_name")
            return self._step_handler.resolve_step(binding.step_name)

        # Unknown namespace
        raise InvalidNamespaceError(
            namespace=binding.namespace,
            context="binding resolution",
            hint=f"Known namespaces: {self.RESERVED_NAMESPACES | {'step'}}",
        )

    def register_namespace(self, name: str, handler: NamespaceHandler) -> None:
        """Register custom namespace handler."""
        if name in self.RESERVED_NAMESPACES:
            raise ValueError(f"Cannot override reserved namespace '{name}'")
        self._handlers[name] = handler

        # Example usage (commented for future reference):
        # ```python
        # # Custom namespace for feature flags
        # class FeatureFlagHandler:
        #     def __init__(self, flag_service):
        #         self._flags = flag_service
        #
        #     def resolve(self, field_path: str) -> Any:
        #         # Return flag service for traversal
        #         return self._flags
        #
        # # Register during pipeline initialization
        # resolver.register_namespace("flagNs", FeatureFlagHandler(flag_service))
        #
        # # Use in pipeline YAML:
        # # handler_inputs:
        # #   enable_feature: flagNs.my_feature_enabled
        # ```


@dataclass
class PathPart:
    """Parsed path component with optional dynamic key lookup."""

    base: str
    dynamic_key: str | None = None
    original: str = ""


def _parse_path_with_dynamic_keys(field_path: str) -> list[PathPart]:
    """
    Parse path into parts, extracting dynamic key lookups.
    Invariant: balanced brackets ⟹ valid parse
    """
    parts = []
    remaining = field_path

    while remaining:
        # Check for dynamic key syntax: base[key]
        match = re.match(r"^([^.\[]*)\[([^\]]+)\]\.?(.*)$", remaining)
        if match:
            base, dynamic_key, rest = match.groups()
            parts.append(
                PathPart(
                    base=base,
                    dynamic_key=dynamic_key,
                    original=f"{base}[{dynamic_key}]",
                )
            )
            remaining = rest
        else:
            # Regular dot-separated part
            if "." in remaining:
                part, remaining = remaining.split(".", 1)
            else:
                part, remaining = remaining, ""
            if part:
                parts.append(PathPart(base=part))

    return parts


def _navigate_single_part(value: Any, part: str, context_path: str) -> Any:
    """Navigate single path component."""
    from .map_reduce import MapOutputCollection

    # Handle MapOutputCollection special cases
    if isinstance(value, MapOutputCollection):
        if part == "*":
            return value.all_outputs()
        if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
            return value.get_output(int(part))
        key_output = value.get_output_by_key(part)
        if key_output is not None:
            return key_output
        available_keys = [k for k in value._key_map.keys() if k is not None]
        hint = (
            f"Available: {sorted(available_keys)}"
            if available_keys
            else "No keys (use index .0, .1 or wildcard .*)"
        )
        raise KeyError(f"Key '{part}' not found. {hint}")

    # Regular navigation
    if isinstance(value, dict):
        if part not in value:
            raise KeyError(f"Key '{part}' not found in dict at '{context_path}'")
        return value[part]
    elif hasattr(value, part):
        return getattr(value, part)
    else:
        raise AttributeError(f"No attribute or key '{part}' in {type(value).__name__}")


def _traverse_with_dynamic_keys(
    root: Any,
    field_path: str,
    step_name: str | None,
    field_name: str | None,
    binding_repr: str | None,
    resolver: "NamespaceResolver | None",
) -> Any:
    """
    Traverse path with dynamic key resolution.
    Invariant: resolver ≠ None when dynamic_key present
    """
    from ..schemas import InputBinding

    parts = _parse_path_with_dynamic_keys(field_path)
    value = root

    for part_info in parts:
        if part_info.dynamic_key:
            # Navigate to base field first
            if part_info.base:
                value = _navigate_single_part(value, part_info.base, part_info.base)

            # Resolve dynamic key binding
            if not resolver:
                raise ValueError(
                    f"Dynamic key lookup requires resolver: {part_info.original}"
                )
            key_binding = InputBinding.parse(part_info.dynamic_key)
            key_root = resolver.resolve(key_binding)
            key = traverse_path(key_root, key_binding.field_path, resolver=resolver)

            # Lookup in dict
            if not isinstance(value, dict):
                raise TypeError(
                    f"Dynamic key lookup requires dict, got {type(value).__name__}"
                )
            if key not in value:
                raise KeyError(
                    f"Key '{key}' not found in dict. Available: {sorted(value.keys())}"
                )
            value = value[key]
        else:
            value = _navigate_single_part(value, part_info.base, field_path)

    return value


def traverse_path(
    root: Any,
    field_path: str,
    step_name: str | None = None,
    field_name: str | None = None,
    binding_repr: str | None = None,
    resolver: "NamespaceResolver | None" = None,
) -> Any:
    """
    Navigate dot-separated path with wildcard and dynamic key support.

    NEW: Dynamic key lookup syntax:
        dict_field[binding] → resolve binding, use result as key

    Examples:
        optionsNs.mapping[mapNs.iteration.key]
        → resolve mapNs.iteration.key to "qwen"
        → return mapping["qwen"]

    Wildcards:
    - step.* → all outputs (for MapOutputCollection)
    - step.*.json.field → collect field from each output
    - step.0, step.-1 → indexed access

    Performance: O(N) for wildcard traversal where N = collection size
    - 10 items: ~0.01ms overhead
    - 100 items: ~0.1ms overhead
    - Acceptable for typical fan-out sizes (10-100)

    Async-safety: Pure function, no side effects.

    Args:
        root: Object to traverse
        field_path: Dot-separated path (e.g., "json.statements")
        step_name: Optional step name for error context
        field_name: Optional field name for error context
        binding_repr: Optional string representation of binding for errors
        resolver: Optional resolver for dynamic key lookup

    Examples:
        traverse_path(step_output, "json.statements") → step_output.json["statements"]
        traverse_path(map_collection, "*.json.score") → [0.8, 0.9, 0.7]

    Raises:
        BindingResolutionError: If traversal fails and context provided
        KeyError/AttributeError: If traversal fails without context
    """
    # Defer import to avoid circular dependency
    from .map_reduce import MapOutputCollection

    if not field_path:
        return root

    # Check for dynamic key syntax
    if "[" in field_path:
        return _traverse_with_dynamic_keys(
            root, field_path, step_name, field_name, binding_repr, resolver
        )

    parts = field_path.split(".")
    value = root

    try:
        for i, part in enumerate(parts):
            # Handle wildcard for MapOutputCollection
            if part == "*" and isinstance(value, MapOutputCollection):
                # Collect remainder path from all outputs
                remainder = ".".join(parts[i + 1 :])
                if not remainder:
                    return value.all_outputs()
                return [
                    traverse_path(
                        output,
                        remainder,
                        step_name=step_name,
                        field_name=field_name,
                        binding_repr=binding_repr,
                        resolver=resolver,
                    )
                    for output in value.all_outputs()
                ]

            # Handle indexed access for MapOutputCollection
            if isinstance(value, MapOutputCollection):
                if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                    value = value.get_output(int(part))
                    continue
                # Try key-based access (for dict-based map_over)
                key_output = value.get_output_by_key(part)
                if key_output is not None:
                    value = key_output
                    continue
                # Key not found - raise clear error
                current_path = ".".join(parts[: i + 1])
                available_keys = [k for k in value._key_map.keys() if k is not None]
                if available_keys:
                    raise KeyError(
                        f"Key '{part}' not found at '{current_path}'. "
                        f"Available: {sorted(available_keys)}"
                    )
                else:
                    raise KeyError(
                        f"Key '{part}' not found at '{current_path}'. "
                        f"No keys (use index .0, .1 or wildcard .*)"
                    )

            # Regular navigation
            if isinstance(value, dict):
                if part not in value:
                    current_path = ".".join(parts[: i + 1])
                    raise KeyError(
                        f"Key '{part}' not found in dict at path '{current_path}'"
                    )
                value = value[part]
            elif hasattr(value, part):
                value = getattr(value, part)
            else:
                current_path = ".".join(parts[: i + 1])
                raise AttributeError(
                    f"No attribute or key '{part}' in {type(value).__name__} "
                    f"at path '{current_path}'"
                )

        return value

    except (KeyError, AttributeError) as e:
        # If context provided, wrap in BindingResolutionError
        if step_name and field_name:
            raise BindingResolutionError(
                step_name=step_name,
                field_name=field_name,
                binding_repr=binding_repr or field_path,
                reason=str(e),
            )
        # Otherwise re-raise original error
        raise


def resolve_model_alias(
    model_key: str,
    context: Any,
    *,
    domain: str | None = None,
) -> str:
    """
    Resolve model alias to full model ID via pipeline registry.

    Universal utility for ANY handler (BaseHandler inheritance not required).
    Use this when you need the full model ID for non-invocation purposes
    (e.g., matching verifier results, config lookups).

    For handlers using BaseHandler._call_model(), alias resolution is automatic.

    Args:
        model_key: Model alias (e.g., "qwen_math") or full ID
        context: PipelineContext with _registry attribute
        domain: Override domain (defaults to context.pipeline.domain)

    Returns:
        Full model ID (e.g., "llama-cpp/qwen2.5-7b-instruct-q4-k-m")

    Raises:
        KeyError: If alias not found in registry

    Example:
        from systems.pipeline.core.execution.resolver import resolve_model_alias

        math_authority_model = resolve_model_alias("qwen_math", context)
        verdict = verifier_results.get(math_authority_model, {})
    """
    registry = context._registry
    resolved_domain = domain or context.pipeline.domain

    model_config = registry.get_model_config(model_key, domain=resolved_domain)
    return model_config.model
