"""Pipeline execution context passed through step handlers."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .step_output import MapIterationState, StepOutput

if TYPE_CHECKING:
    from fastapi import Request

    from ..execution.map_reduce import MapOutputCollection
    from ..execution.proxy_client import ProxyClient
    from ..schemas import PipelineSpec, SourceInput
    from .builtin.types import ModelCallResult
    from .events.recorder import EventRecorder


@dataclass
class PipelineContext:
    """
    Execution context passed through pipeline steps.

    CONCURRENCY INVARIANT:
    - Handlers have READ-ONLY access to outputs
    - Only DAGExecutor calls set_output() (single-writer)
    - Handlers MUST return StepOutput, never write directly

    Invariants:
    - ∀ step_id ∈ outputs.keys() ⟹ step completed successfully
    - source_text is immutable during execution
    - pipeline is immutable during execution

    Dependencies are injected via executor, not constructed by handlers.
    This enables proper testing and avoids import-time instantiation.
    """

    # Immutable inputs
    pipeline: PipelineSpec
    source_text: str
    http_request: Request

    # Mutable step outputs - ONLY DAGExecutor writes here
    outputs: dict[str, StepOutput] = field(default_factory=dict)

    # Persistent thread chat_id identifier (None for non-chat or stateless pipelines)
    chat_id: str | None = None
    # Team-dispatch thread key (Phase D); distinct from agent-bus ``to_thread`` id
    dispatch_thread_id: str | None = None

    # Execution metadata
    execution_id: str = ""
    started_at: datetime = field(default_factory=datetime.now)

    # Runtime options from HTTP request
    runtime_options: dict[str, Any] = field(default_factory=dict)

    # Injected dependencies (set by executor, not handlers)
    _registry: object | None = None
    _request_executor: object | None = None
    _proxy: object | None = None

    # Model invocation client (replaces ModelInvoker)
    proxy_client: ProxyClient | None = None

    # Gateway tracking (for eviction protection)
    selected_gateway_instance: str | None = None  # Gateway name from parent request

    # Per-iteration request ID for cancellation tracking (set by MapExecutor)
    # Becomes proxy request_id via X-Internal-Request-ID header
    map_iteration_request_id: str | None = None

    # Pre-generated request ID for the first model call of a map iteration.
    # Consumed once by call_model → proxy_client for request.processing correlation.
    # Subsequent calls within the same iteration generate fresh UUIDs.
    inference_request_id: str | None = None

    # Map iteration state (set by MapExecutor for provenance tracking)
    _map_state: MapIterationState | None = None

    # Event recorder for pipeline observability (set by executor)
    _recorder: EventRecorder | None = None

    # Auto-tracking: model calls made during current step, keyed by step name.
    # Using a dict prevents concurrent steps from contaminating each other's
    # call lists: step A draining its own key does not remove step B's entries.
    # Cleared per-key by DAGExecutor at each step boundary.
    _step_model_calls: dict[str, list[ModelCallResult]] = field(default_factory=dict)

    # Per-step model override for executor-level fallback.
    # Keyed by step_name → full model ID. When set, the generate handler
    # uses this model directly instead of resolving step.model_ref via registry.
    # Parallel-step safe: each step writes to its own key.
    _step_model_override: dict[str, str] = field(default_factory=dict)

    # Full conversation history (None for non-chat pipelines)
    _messages: list[dict[str, Any]] | None = None

    @property
    def recorder(self):
        """Event recorder for pipeline observability. May be None if not configured."""
        return self._recorder

    def record_model_call(self, call_result: ModelCallResult, step_name: str) -> None:
        """Record a model call for automatic token aggregation."""
        self._step_model_calls.setdefault(step_name, []).append(call_result)

    def drain_step_calls(self, step_name: str) -> list[ModelCallResult]:
        """Return and clear calls recorded for step_name. DAGExecutor only."""
        return self._step_model_calls.pop(step_name, [])

    def with_map_iteration_request_id(
        self, map_iteration_request_id: str
    ) -> PipelineContext:
        """
        Return context copy with map_iteration_request_id set.

        Used by MapExecutor to pass pre-generated request ID to handlers.
        Each map iteration gets its own context with unique ID that becomes
        the proxy's request_id via X-Internal-Request-ID header.

        Args:
            map_iteration_request_id: Pre-generated UUID for cancellation tracking

        Returns:
            Shallow copy of context with map_iteration_request_id set
        """
        return dataclasses.replace(
            self,
            map_iteration_request_id=map_iteration_request_id,
        )

    def with_inference_request_id(self, inference_request_id: str) -> PipelineContext:
        """
        Return context copy with pre-generated inference request ID.

        Used by MapExecutor so call_model can pass the ID to proxy_client,
        enabling request.processing event correlation before the HTTP call.
        Consumed once by call_model; subsequent calls generate fresh UUIDs.
        """
        return dataclasses.replace(self, inference_request_id=inference_request_id)

    def with_map_state(self, map_state: MapIterationState) -> PipelineContext:
        """
        Return context copy with map iteration state set.

        Used by MapExecutor to pass iteration context for provenance tracking.

        Args:
            map_state: Map iteration state (source step, key, index)

        Returns:
            Shallow copy of context with _map_state set
        """
        return dataclasses.replace(self, _map_state=map_state)

    def get_proxy_client(self) -> ProxyClient:
        """
        Get ProxyClient for internal Stargate requests.

        Returns:
            Shared ProxyClient instance

        Raises:
            RuntimeError: If proxy client not configured
        """
        if self.proxy_client is None:
            raise RuntimeError(
                "ProxyClient not configured in PipelineContext. "
                "Ensure pipeline executor initializes proxy client."
            )
        return self.proxy_client

    @property
    def source(self) -> SourceInput:
        """Pipeline input data as SourceInput object."""
        from ..schemas import SourceInput

        return SourceInput(text=self.source_text, messages=self._messages)

    @property
    def messages(self) -> list[dict[str, Any]] | None:
        """Full conversation history. None for non-chat requests."""
        return self._messages

    @property
    def options(self) -> dict[str, Any]:
        """
        Pipeline options as dict.

        Merges YAML defaults with runtime options from HTTP request.
        Runtime options override YAML defaults.

        Invariant: ∀ key: options[key] = runtime_options.get(key, yaml_defaults[key])

        Returns dict representation for compatibility with PipelineContextProtocol.
        """
        yaml_options = self.pipeline.options.to_context_dict()
        # Runtime options override YAML defaults
        return {**yaml_options, **self.runtime_options}

    @property
    def domain(self) -> str:
        """Pipeline domain (type)."""
        return self.pipeline.domain

    def get_output(self, step_id: str) -> StepOutput | None:
        """Get output from a completed step (read-only)."""
        return self.outputs.get(step_id)

    def get_outputs(self, step_ids: list[str]) -> dict[str, StepOutput]:
        """Get outputs from multiple steps (read-only)."""
        return {sid: self.outputs[sid] for sid in step_ids if sid in self.outputs}

    def get_text_outputs(self, step_ids: list[str]) -> dict[str, str]:
        """Get text outputs from multiple steps (for candidates)."""
        return {sid: self.outputs[sid].text for sid in step_ids if sid in self.outputs}

    def set_output(
        self, step_id: str, output: StepOutput | MapOutputCollection
    ) -> None:
        """
        Record output from a completed step.

        WARNING: Only DAGExecutor should call this method.
        Handlers MUST return StepOutput, not call this directly.

        Accepts MapOutputCollection for map steps (stored for wildcard resolution
        by the input resolver). Provenance is only populated for StepOutput.
        """
        if isinstance(output, StepOutput):
            output.step_id = step_id

            if output.provenance is None and output.model_id:
                from provenance import create_provenance

                output.provenance = create_provenance(
                    model_id=output.model_id,
                    step_id=output.step_id,
                ).to_dict()

        self.outputs[step_id] = output

    def get_option(self, key: str, default: Any = None) -> Any:
        """Get option value."""
        return self.options.get(key, default)

    @property
    def gateway_manager(self):
        """Get gateway manager from proxy (public accessor)."""
        return self._proxy.gateway_manager

    @property
    def requirements_provider(self):
        """Get resource requirements provider (public accessor)."""
        return self._proxy.resource_aware_model_manager._loading_ops._get_requirements

    @property
    def routing_operations(self):
        """Get routing operations (public accessor)."""
        return self._proxy.resource_aware_model_manager._routing_ops
