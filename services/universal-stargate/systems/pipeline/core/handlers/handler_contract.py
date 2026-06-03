"""Abstract handler base and StepHandler protocol."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from .pipeline_context import PipelineContext
from .step_output import StepOutput

if TYPE_CHECKING:
    from ..schemas import StepConfig


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
    - `StepHandler` (Protocol) - Duck-typing alternative
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
