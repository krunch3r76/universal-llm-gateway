"""
Request context utilities for unified parameter passing.
"""

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

from universal_logging import get_logger

if TYPE_CHECKING:
    from gateways import GatewayInstance

logger = get_logger(__name__)


@dataclass
class ForwardContext:
    """Unified context for forward request operations."""

    request_id: str
    gateway_instance: Optional["GatewayInstance"] = None  # NEW: Selected gateway
    model_name: str | None = None
    metadata: dict[str, Any] | None = None
    middleware_actions: list | None = None
    token_metrics: dict[str, Any] | None = None
    modified_request: dict[str, Any] | None = None
    original_request: dict[str, Any] | None = None

    # Helper property for accessing gateway URL
    @property
    def gateway_url(self) -> str | None:
        return self.gateway_instance.config.base_url if self.gateway_instance else None

    # Helper property for accessing gateway name
    @property
    def gateway_name(self) -> str | None:
        return self.gateway_instance.config.name if self.gateway_instance else None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.middleware_actions is None:
            self.middleware_actions = []


class RequestContextBuilder:
    """Builder for creating standardized request contexts."""

    @staticmethod
    def from_request_context(
        context,
        model_metadata: dict[str, Any],
        gateway_instance: Optional["GatewayInstance"] = None,  # NEW
    ) -> ForwardContext:
        """Build ForwardContext from RequestContext."""
        return ForwardContext(
            request_id=context.request_id,
            gateway_instance=gateway_instance,  # NEW
            model_name=str(context.selected_model),  # Convert ModelId to string
            metadata=model_metadata,
            middleware_actions=context.middleware_actions.copy(),
            token_metrics=(
                context.token_metrics.dict() if context.token_metrics else None
            ),
            modified_request=context.modified_request,
            original_request=context.original_request,
        )

    @staticmethod
    def minimal(request_id: str, model_name: str | None = None) -> ForwardContext:
        """Create minimal context for simple requests."""
        return ForwardContext(request_id=request_id, model_name=model_name)


def extract_model_name(
    request_context: ForwardContext | None, content: bytes | None
) -> str | None:
    """
    Extract model name from context or request body.

    Args:
        request_context: Forward context containing model info
        content: Request body to parse for model name

    Returns:
        Model name if found, None otherwise
    """
    # First try context
    if request_context and request_context.model_name:
        return request_context.model_name

    # Fallback to parsing request body
    if content:
        try:
            request_body = json.loads(content.decode("utf-8"))
            return request_body.get("model")
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.debug(f"Failed to parse model from request body: {e}")
        except Exception as e:
            logger.warning(f"Unexpected error parsing request body: {e}")

    return None
