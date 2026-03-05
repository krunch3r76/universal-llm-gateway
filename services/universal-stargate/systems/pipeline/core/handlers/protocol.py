"""
Step handler protocol and execution context.

Defines the interface all step handlers must implement.

Invariants:
- ∀ handler: handler.step_type ∈ str
- ∀ handler: handler.execute() returns StepOutput (never writes to context)
- ∀ step_id ∈ context.outputs: step completed successfully
- Only DAGExecutor writes to context.outputs (single-writer)
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import Request

    from ..execution.proxy_client import ProxyClient
    from ..schemas import PipelineSpec, SourceInput, StepConfig


@dataclass
class MapIterationState:
    """
    State for current map iteration.

    Populated by MapExecutor to enable provenance tracking through map steps.
    Handlers can access via context._map_state to determine source step/key.
    """

    source_step_name: str  # e.g., "answer_all"
    iteration_key: str | None  # e.g., "phi" (None for list-based iterations)
    iteration_index: int  # e.g., 0


@dataclass
class StepOutput:
    """
    Output from a single step execution.

    Captures both the result and execution metadata.

    The 'raw' field contains the unprocessed model response.
    The 'json' field contains JSON-parsed response data (when json_schema specified).
    The 'text' property returns the best text representation.

    Optional prompt fields (system_prompt, user_prompt) capture the exact
    prompts sent to the model for debugging and execution summaries.

    Invariant: ∀ output.json: (json_schema specified) ⟹ (json ∈ dict[str, Any] | None)
    """

    raw: str
    json: dict[str, Any] | None = None
    json_parse_error: str | None = None  # Why json is null when json_schema was set
    prompt_tokens: int = 0  # Tokens in prompt (system + user messages)
    completion_tokens: int = 0  # Tokens in model response
    latency_ms: float = 0.0
    model_call_count: int = 0  # Number of _call_model() invocations for this step
    model_id: str | None = None
    step_id: str = ""
    error: str | None = None  # Non-None if step failed but produced partial output

    # Prompt capture (optional, for execution summaries and debugging)
    system_prompt: str | None = None  # System message sent to model (if any)
    user_prompt: str | None = None  # User message sent to model

    # Generation parameters (actual values used during execution)
    temperature: float | None = None  # Temperature used for generation
    max_tokens: int | None = None  # Max tokens used for generation

    # Full request body sent to LLM (for complete reproducibility)
    request_body: dict[str, Any] | None = None

    # Embedded provenance (auto-populated from model_id + step_id)
    provenance: dict[str, Any] | None = None

    def __post_init__(self):
        """Auto-populate provenance from model_id and step_id if not set."""
        if self.provenance is None and self.model_id and self.step_id:
            from provenance import create_provenance

            prov = create_provenance(
                model_id=self.model_id,
                step_id=self.step_id,
            )
            self.provenance = prov.to_dict()

    @property
    def text(self) -> str:
        """
        Get text content, preferring json fields if available.

        Domain handlers populate 'json' with extracted content.
        This property returns the best text representation:
        1. json["translation"] if present (translation domain)
        2. json["text"] if present (generic)
        3. raw content as fallback
        """
        if self.json:
            if "translation" in self.json:
                return self.json["translation"]
            if "text" in self.json:
                return self.json["text"]
        return self.raw

    @property
    def has_content(self) -> bool:
        """Check if output has non-empty content."""
        return bool(self.text.strip())

    def to_checkpoint_data(self) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        """
        Extract checkpoint data from output.

        Returns:
            Tuple of (raw_text, json_data, metadata)
        """
        metadata: dict[str, Any] = {
            "latency_ms": self.latency_ms,
            "model_id": self.model_id,
            "step_id": self.step_id,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        # Remove None values for cleaner storage
        metadata = {k: v for k, v in metadata.items() if v is not None}

        return self.raw, self.json, metadata


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

    # Execution metadata
    execution_id: str = ""
    started_at: datetime = field(default_factory=datetime.now)

    # Runtime options from HTTP request
    runtime_options: dict[str, Any] = field(default_factory=dict)

    # Injected dependencies (set by executor, not handlers)
    _registry: Any = None
    _request_executor: Any = None
    _proxy: Any = None

    # Model invocation client (replaces ModelInvoker)
    proxy_client: Any = None  # ProxyClient instance

    # Gateway tracking (for eviction protection)
    selected_gateway_instance: str | None = None  # Gateway name from parent request

    # Per-iteration request ID for cancellation tracking (set by MapExecutor)
    # Becomes proxy request_id via X-Internal-Request-ID header
    map_iteration_request_id: str | None = None

    # Map iteration state (set by MapExecutor for provenance tracking)
    _map_state: MapIterationState | None = None

    # Event recorder for pipeline observability (set by executor)
    _recorder: Any = None  # EventRecorder instance

    # Auto-tracking: model calls made during current step, keyed by step name.
    # Using a dict prevents concurrent steps from contaminating each other's
    # call lists: step A draining its own key does not remove step B's entries.
    # Cleared per-key by DAGExecutor at each step boundary.
    _step_model_calls: dict[str, list[Any]] = field(default_factory=dict)

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

    def record_model_call(self, call_result: Any, step_name: str) -> None:
        """Record a model call for automatic token aggregation."""
        self._step_model_calls.setdefault(step_name, []).append(call_result)

    def drain_step_calls(self, step_name: str) -> list[Any]:
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

    def set_output(self, step_id: str, output: StepOutput) -> None:
        """
        Record output from a completed step.

        WARNING: Only DAGExecutor should call this method.
        Handlers MUST return StepOutput, not call this directly.
        """
        output.step_id = step_id

        # Populate provenance if not already set and model_id is present
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


class AbstractStepHandler(ABC):
    """
    Abstract base class for pipeline step handlers.

    This class documents the complete contract that all handlers must implement.
    Handlers can inherit from this class for IDE support and explicit contract
    verification, or implement the StepHandler Protocol for duck typing.

    Contract Requirements:
    ----------------------

    **Class Attributes (Required):**

    - `step_type: str` - Identifier matching YAML `type:` field
      - Used for handler registration and routing
      - Must be unique within domain
      - Example: `step_type = "generate"`

    **Methods:**

    - `execute()` - Required, async. Main execution logic.
    - `validate()` - Optional. Configuration validation at load time.
    - `get_required_placeholders()` - Optional. Template placeholder requirements.

    Invariants:
    -----------

    - ∀ execute(): returns StepOutput ∧ ¬writes_to_context.outputs
    - Handlers are stateless (instantiated fresh per-execution)
    - All I/O operations must be async
    - Dependencies accessed via context, not constructor

    Example:
    --------

    ```python
    class MyHandler(AbstractStepHandler):
        step_type = "my_step"

        async def execute(
            self,
            step: StepConfig,
            context: PipelineContext,
        ) -> StepOutput:
            # Access dependencies via context
            client = context.get_proxy_client()

            # Do work (all I/O must be async)
            result = await some_async_operation()

            # Return StepOutput - NEVER call context.set_output()
            return StepOutput(
                raw=result,
                json={"text": result},  # For .text property access
            )

        def validate(self, step: StepConfig) -> list[str]:
            errors = []
            if not step.model_ref:
                errors.append(f"Step '{step.id}' missing model_ref")
            return errors
    ```

    Anti-Patterns (FORBIDDEN):
    --------------------------

    ```python
    # ❌ Writing to context.outputs directly
    async def execute(self, step, context):
        context.set_output(step.id, output)  # WRONG!
        return output

    # ❌ Blocking I/O
    async def execute(self, step, context):
        with open("file.txt") as f:  # WRONG - use aiofiles
            data = f.read()

    # ❌ Constructor dependencies
    def __init__(self, registry, client):  # WRONG
        self.registry = registry  # Access via context instead

    # ❌ Passing text= to StepOutput
    return StepOutput(raw="x", text="x")  # TypeError!
    ```

    See Also:
    ---------
    - `BaseHandler` - Adds utility methods (_call_model, _build_generation_params)
    - `StepHandler` (Protocol) - Duck-typing alternative
    - `GenericGenerateHandler` - Reference implementation
    """

    # Required class attribute - subclasses MUST set this
    step_type: str

    @abstractmethod
    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """
        Execute the step and return output.

        This is the main entry point for step execution. The handler receives
        the step configuration and pipeline context, performs its work, and
        returns a StepOutput object.

        Args:
            step: Step configuration from pipeline YAML. Key attributes:
                - step.id: Step identifier
                - step.type: Step type (matches step_type class attr)
                - step.model_ref: Model reference (if applicable)
                - step.prompt_ref: Prompt reference (if applicable)
                - step.handler_inputs: Dict of input bindings
                - step.handler_outputs: Dict of output bindings
                - step.generation_parameters: Dict of generation params
                - step.timeout_seconds: Step timeout (optional)
                - step.retry_policy: Retry configuration (optional)

            context: Pipeline execution context. Key attributes:
                - context.source_text: Original user input
                - context.outputs: Read-only dict of completed step outputs
                - context.options: Pipeline options (YAML + runtime)
                - context.proxy_client: Model invocation client
                - context._registry: Prompt/model registry
                - context.execution_id: Unique execution identifier

        Returns:
            StepOutput with:
                - raw: Raw response content (required, str)
                - json: Parsed JSON data (optional, dict)
                - prompt_tokens: Prompt token count (optional)
                - completion_tokens: Completion token count (optional)
                - latency_ms: Execution time in milliseconds (optional)
                - model_id: Model used for generation (optional)
                - error: Error message if partial failure (optional)
                - system_prompt: System prompt sent (optional, for debugging)
                - user_prompt: User prompt sent (optional, for debugging)

        Raises:
            ValueError: For configuration errors
            ProxyClientError: For model invocation failures
            Any exception: Will be caught by executor, may trigger retry

        Important:
            - Do NOT call context.set_output() - return StepOutput
            - The executor writes your output to context.outputs
            - Access previous outputs via context.get_output(step_id)
            - All I/O operations must be async
            - Handler instances are created fresh for each execution

        Note on StepOutput.text:
            The .text property is COMPUTED, not a constructor parameter!
            It reads from json["translation"], json["text"], or raw (in order).
            To set the text returned by .text, use:
                StepOutput(raw="text", json={"text": "text"})
            NOT:
                StepOutput(raw="text", text="text")  # TypeError!
        """
        ...

    def validate(self, step: StepConfig) -> list[str]:
        """
        Validate step configuration at pipeline load time.

        This method is called during pipeline validation, before any execution.
        Use it to check that required fields are present and values are valid.

        Args:
            step: Step configuration to validate

        Returns:
            List of validation error messages. Empty list = valid.
            Each error should clearly identify the issue and step.

        Default:
            Returns empty list (all configurations valid).

        Example:
            ```python
            def validate(self, step: StepConfig) -> list[str]:
                errors = []
                if not step.model_ref:
                    errors.append(f"Step '{step.id}' missing model_ref")
                if not step.prompt_ref:
                    errors.append(f"Step '{step.id}' missing prompt_ref")
                if step.handler_inputs and "text" not in step.handler_inputs:
                    errors.append(f"Step '{step.id}' requires 'text' input")
                return errors
            ```

        Note:
            Validation runs at load time, not execution time.
            Runtime errors should raise exceptions in execute().
        """
        return []

    def get_required_placeholders(self) -> set[str]:
        """
        Get placeholder names required in prompt templates.

        If your handler uses prompt templates with placeholders like
        {{text}} or {{candidates}}, return the set of required names.
        This enables template validation at load time.

        Returns:
            Set of placeholder names (e.g., {"text", "candidates"})
            Empty set = no placeholders required.

        Default:
            Returns empty set.

        Example:
            ```python
            def get_required_placeholders(self) -> set[str]:
                return {"text", "source_language", "target_language"}
            ```
        """
        return set()


@runtime_checkable
class StepHandler(Protocol):
    """
    Protocol for step type handlers.

    Implementations must:
    - Set step_type class attribute
    - Implement async execute() that RETURNS StepOutput
    - Optionally implement validate() and get_required_placeholders()

    IMPORTANT: Handlers must NEVER write to context.outputs directly.
    They return StepOutput; the executor writes to context.
    """

    step_type: str

    async def execute(
        self,
        step: StepConfig,
        context: PipelineContext,
    ) -> StepOutput:
        """
        Execute the step and return output.

        Args:
            step: Step specification from pipeline config
            context: Execution context with inputs and previous outputs

        Returns:
            StepOutput with result and metadata

        IMPORTANT: Do NOT call context.set_output(). Return the StepOutput.
        """
        ...

    def validate(self, step: StepConfig) -> list[str]:
        """
        Validate step configuration at load time.

        Args:
            step: Step specification to validate

        Returns:
            List of validation error messages (empty = valid)
        """
        return []

    def get_required_placeholders(self) -> set[str]:
        """
        Get placeholder names required in prompt templates.

        Returns:
            Set of placeholder names (e.g., {"text", "candidates"})
        """
        return set()
