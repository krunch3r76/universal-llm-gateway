"""Map iteration preparation: input resolution, model selection, step creation."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ....schemas import InputBinding, StepConfig

logger = logging.getLogger(__name__)


class MapIterationPreparer:
    """Resolves map_over bindings, prepares per-iteration inputs and step configs."""

    def __init__(
        self,
        step: StepConfig,
        map_config: Any,
        resolver: Any,
        handler: Any,
    ) -> None:
        self._step = step
        self._map_config = map_config
        self._resolver = resolver
        self._handler = handler

    def extract_source_step_name(self) -> str | None:
        """
        Extract source step name from map_over binding.

        For map_over: { answer: answer_all.* }
        Returns: "answer_all"

        For map_over: { model: optionsNs.models }
        Returns: None (not a step reference)
        """
        if not self._map_config or not self._map_config.map_over:
            return None

        _field_name, binding = next(iter(self._map_config.map_over.items()))
        if hasattr(binding, "namespace") and binding.namespace == "step":
            return binding.step_name
        return None

    def select_from_pool(
        self,
        pool: list[str],
        originator: str | None,
        exclude_self: bool,
        selection: str,
        index: int,
    ) -> str:
        """
        Select model from pool for iteration.

        Invariant: |pool| > 0 => returns valid model

        Args:
            pool: Available models
            originator: Current iteration key (model to potentially exclude)
            exclude_self: Whether to exclude originator from candidates
            selection: "random", "rotate", or "first"
            index: Iteration index (for rotate determinism)
        """
        candidates = pool

        if exclude_self and originator:
            candidates = [m for m in pool if m != originator]
            if not candidates:
                candidates = [originator]
                logger.debug(
                    "[%s] Pool exhausted after exclude_self, using originator: %s",
                    self._step.name,
                    originator,
                )

        if selection == "random":
            import random

            return random.choice(candidates)
        elif selection == "rotate":
            return candidates[index % len(candidates)]
        else:  # first
            return candidates[0]

    def resolve_map_over(
        self,
        bindings: dict[str, InputBinding],
    ) -> list[tuple[int, Any, str | None]]:
        """
        Resolve map_over bindings to iteration items.

        Returns list of (index, value, key) tuples.

        Supports:
        - list/dict from optionsNs
        - MapOutputCollection via step.* (wildcard means iterate collection)
        """
        from ...resolver import traverse_path
        from ..map_output_collection import MapOutputCollection

        if len(bindings) != 1:
            raise NotImplementedError("Multi-field map_over not yet supported")

        field_name, binding = next(iter(bindings.items()))
        root = self._resolver.resolve(binding)

        if binding.field_path == "*" and isinstance(root, MapOutputCollection):
            value = root
        else:
            value = (
                traverse_path(root, binding.field_path, resolver=self._resolver)
                if binding.field_path
                else root
            )

        if isinstance(value, list):
            return [(i, v, None) for i, v in enumerate(value)]
        elif isinstance(value, dict):
            return [(i, v, k) for i, (k, v) in enumerate(value.items())]
        elif isinstance(value, MapOutputCollection):
            return [(i, v, k) for i, (k, v) in enumerate(value.items())]
        else:
            raise TypeError(
                f"map_over field '{field_name}' must resolve to list, dict, or "
                f"MapOutputCollection, got {type(value).__name__}"
            )

    def build_pool_assignments(
        self,
        iteration_items: list[tuple[int, Any, str | None]],
        runtime: Any,
    ) -> dict[int, str]:
        """
        Pre-compute model assignments for all iterations.

        Handles both model_requirements (capability-based) and
        model_pool (explicit list) assignment strategies.
        Returns empty dict if no pool is configured.
        """
        pool_assignments: dict[int, str] = {}

        if not self._map_config.model_pool and self._map_config.model_requirements:
            from ...requirements_resolver import resolve_model_requirements

            proxy = getattr(runtime, "_proxy", None)
            resolved_ids = resolve_model_requirements(
                self._map_config.model_requirements, proxy
            )
            if resolved_ids:
                pool = resolved_ids
                for idx, _value, key in iteration_items:
                    pool_assignments[idx] = self.select_from_pool(
                        pool=pool,
                        originator=key,
                        exclude_self=self._map_config.exclude_self,
                        selection=self._map_config.selection,
                        index=idx,
                    )
                logger.info(
                    "[%s] Model assignments (requirements-derived pool=%s, "
                    "selection=%s): %s",
                    self._step.name,
                    pool,
                    self._map_config.selection,
                    {
                        key: pool_assignments[idx]
                        for idx, _, key in iteration_items
                        if key
                    },
                )

        if self._map_config.model_pool:
            from ...resolver import traverse_path

            pool_binding = self._map_config.model_pool
            pool_root = self._resolver.resolve(pool_binding)
            pool = traverse_path(
                pool_root, pool_binding.field_path, resolver=self._resolver
            )
            if not isinstance(pool, list):
                raise TypeError(
                    f"model_pool must resolve to list, got {type(pool).__name__}"
                )
            if not pool:
                raise ValueError("model_pool resolved to empty list")

            for idx, _value, key in iteration_items:
                pool_assignments[idx] = self.select_from_pool(
                    pool=pool,
                    originator=key,
                    exclude_self=self._map_config.exclude_self,
                    selection=self._map_config.selection,
                    index=idx,
                )
            logger.info(
                "[%s] Model assignments (pool, exclude_self=%s, selection=%s): %s",
                self._step.name,
                self._map_config.exclude_self,
                self._map_config.selection,
                {key: pool_assignments[idx] for idx, _, key in iteration_items if key},
            )

        return pool_assignments

    def prepare_iteration_inputs(
        self,
        index: int,
        value: Any,
        total: int,
        key: str | None = None,
        assigned_model: str | None = None,
    ) -> tuple[Any, dict[str, Any], dict[str, Any], Any]:
        """
        Prepare all inputs for iteration execution.

        Returns:
            Tuple of (iteration_resolver, all_inputs_dict, map_inputs_dict,
            typed_inputs_or_none)

        For map-compatible handlers (with input_type): typed_inputs is
        constructed. For legacy handlers (without input_type): typed_inputs is
        None.

        all_inputs_dict: merged handler_inputs + map_inputs for typed input
        construction. map_inputs_dict: ONLY map_inputs for step override.
        """
        from ....schemas import MapState
        from ...resolver import traverse_path

        map_state = MapState(
            iteration_index=index,
            iteration_value=value,
            iteration_key=key,
            iteration_total=total,
            assigned_model=assigned_model,
        )
        iter_resolver = self._resolver.with_map_context(map_state)

        map_input_values: dict[str, Any] = {}
        for field, binding in self._map_config.map_inputs.items():
            root = iter_resolver.resolve(binding)
            map_input_values[field] = traverse_path(
                root, binding.field_path, resolver=iter_resolver
            )

        handler_input_values: dict[str, Any] = {}
        for field, binding in self._step.handler_inputs.items():
            root = iter_resolver.resolve(binding)
            handler_input_values[field] = traverse_path(
                root, binding.field_path, resolver=iter_resolver
            )

        all_inputs = {**handler_input_values, **map_input_values}
        typed_inputs = (
            self._handler.input_type(**all_inputs)
            if hasattr(self._handler, "input_type")
            else None
        )
        return iter_resolver, all_inputs, map_input_values, typed_inputs

    def create_iteration_step(
        self, map_inputs: dict[str, Any], assigned_model: str | None = None
    ) -> StepConfig:
        """
        Create step config with map_inputs applied as overrides.

        Template placeholder values are stored in resolved_map_inputs.
        generation_parameters merges with step-level params instead of replacing.
        If assigned_model provided and model_ref not in map_inputs, applies it.
        """
        step_overrides: dict[str, Any] = {}
        template_inputs: dict[str, Any] = {}

        for field, value in map_inputs.items():
            if hasattr(self._step, field) and field != "resolved_map_inputs":
                if field == "generation_parameters" and isinstance(value, dict):
                    base_params = getattr(self._step, field, {}) or {}
                    merged_params = {**base_params, **value}
                    step_overrides[field] = merged_params
                    logger.debug(
                        "[%s] Merging step.%s: base=%r + override=%r = %r",
                        self._step.name,
                        field,
                        base_params,
                        value,
                        merged_params,
                    )
                else:
                    step_overrides[field] = value
                    logger.debug(
                        "[%s] Overriding step.%s = %r for iteration",
                        self._step.name,
                        field,
                        value,
                    )
            else:
                template_inputs[field] = value
                logger.debug(
                    "[%s] Template input %s = %r for iteration",
                    self._step.name,
                    field,
                    value,
                )

        if assigned_model and "model_ref" not in step_overrides:
            step_overrides["model_ref"] = assigned_model
            logger.debug(
                "[%s] Using pool-assigned model_ref=%r",
                self._step.name,
                assigned_model,
            )

        if template_inputs:
            step_overrides["resolved_map_inputs"] = template_inputs

        if step_overrides:
            return self._step.model_copy(update=step_overrides)
        return self._step
