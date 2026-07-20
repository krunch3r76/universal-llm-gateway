"""Config-manager result and error types.

ConfigValidationError, ValidationContext, and ModelOperationResult are the
public types returned or raised by ConfigManager CRUD and validation paths.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal


class ConfigValidationError(Exception):
    """Raised when configuration validation fails"""

    pass


class ValidationContext(Enum):
    """Context for validation operations"""

    NEW = "new"
    UPDATE = "update"


@dataclass
class ModelOperationResult:
    """Result of a model operation"""

    operation: Literal["created", "updated_by_key", "updated_by_path"]
    model_key: str
    requested_key: str | None = None
    _custom_message: str | None = None

    @property
    def message(self) -> str:
        """Generate or return custom message"""
        if self._custom_message:
            return self._custom_message

        if self.operation == "created":
            return f"Created new model '{self.model_key}' with unique path"
        elif self.operation == "updated_by_key":
            return f"Updated existing model '{self.model_key}'"
        else:  # updated_by_path
            return (
                f"Updated existing model '{self.model_key}' with same path "
                f"(requested key '{self.requested_key}' was ignored due to path-based update)"
            )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for API responses"""
        result = {
            "status": "success",
            "model_key": self.model_key,
            "operation": self.operation,
            "message": self.message,
        }

        if self.requested_key and self.requested_key != self.model_key:
            result["requested_key"] = self.requested_key

        return result
