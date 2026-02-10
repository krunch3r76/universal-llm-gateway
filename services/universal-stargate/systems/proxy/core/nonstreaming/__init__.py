"""
Non-streaming request processing subpackage.

Handles request preparation, transformation, building, forwarding, and execution
for non-streaming (synchronous) HTTP responses.

Counterpart: core/streaming/ for streaming responses.
"""

from .builder import RequestBuilder
from .context import RequestContext
from .executor import RequestExecutor
from .forwarder import RequestForwarder
from .preparer import RequestPreparer, validate_and_prepare_model_id
from .response_transform import (
    get_response_data,
    transform_dict_to_prompt_format,
    transform_response_to_prompt_format,
)
from .token_management import apply_token_management
from .transformer import RequestTransformer

__all__ = [
    # Core classes
    "RequestBuilder",
    "RequestContext",
    "RequestExecutor",
    "RequestForwarder",
    "RequestPreparer",
    "RequestTransformer",
    # Functions
    "apply_token_management",
    "get_response_data",
    "transform_dict_to_prompt_format",
    "transform_response_to_prompt_format",
    "validate_and_prepare_model_id",
]
