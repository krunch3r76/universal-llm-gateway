"""Input binding and namespace resolution errors.

Structured errors raised while resolving step input bindings, validating a
resolved value's type against a handler's declared ``input_type``, and
guarding namespace usage. Each serializes via ``to_dict()`` for API response
envelopes and inherits the non-retryable default from :class:`PipelineError`.
"""

from dataclasses import dataclass

from .pipeline_error import PipelineError


@dataclass
class BindingResolutionError(PipelineError):
    """Raised when an input binding cannot be resolved."""

    step_name: str
    field_name: str
    binding_repr: str  # String repr of binding
    reason: str

    def __str__(self) -> str:
        return (
            f"[Step '{self.step_name}'] Cannot resolve input '{self.field_name}'\n"
            f"  Binding: {self.binding_repr}\n"
            f"  Reason: {self.reason}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "BindingResolutionError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "field_name": self.field_name,
            "binding": self.binding_repr,
            "reason": self.reason,
        }


@dataclass
class InputTypeMismatchError(PipelineError):
    """Raised when resolved value doesn't match handler's input_type."""

    step_name: str
    field_name: str
    expected_type: str
    actual_type: str
    value_preview: str

    def __str__(self) -> str:
        preview = (
            self.value_preview[:100] + "..."
            if len(self.value_preview) > 100
            else self.value_preview
        )
        return (
            f"[Step '{self.step_name}'] Type mismatch for input '{self.field_name}'\n"
            f"  Expected: {self.expected_type}\n"
            f"  Got: {self.actual_type}\n"
            f"  Value: {preview}"
        )

    def to_dict(self) -> dict:
        return {
            "error_type": "InputTypeMismatchError",
            "retryable": self.retryable,
            "step_name": self.step_name,
            "field_name": self.field_name,
            "expected_type": self.expected_type,
            "actual_type": self.actual_type,
            "value_preview": self.value_preview[:100],
        }


@dataclass
class InvalidNamespaceError(PipelineError):
    """Raised when a namespace is used in invalid context."""

    namespace: str
    context: str
    hint: str = ""

    def __str__(self) -> str:
        msg = f"Invalid namespace '{self.namespace}' in context: {self.context}"
        if self.hint:
            msg += f"\n  Hint: {self.hint}"
        return msg

    def to_dict(self) -> dict:
        return {
            "error_type": "InvalidNamespaceError",
            "retryable": self.retryable,
            "namespace": self.namespace,
            "context": self.context,
            "hint": self.hint,
        }
