"""Count tokens RPC handler."""

from universal_logging import get_logger
from typing import Any

from universal_protocol.errors import RPCError

from .log_format import make_log_prefix
from .tokenizer_hook import get_tokenizer_callback

logger = get_logger(__name__)


async def handle_count_tokens(params: dict[str, Any]) -> dict[str, Any]:
    """Handle count_tokens RPC method.

    Inputs:
        params: Method parameters containing:
            - text: Input text to tokenize
            - model: (optional) Model name for tokenization
            - correlation_id: Optional correlation ID

    Outputs:
        Dict with count (int) and method ("exact")

    Raises:
        RPCError: If text is missing, invalid, or tokenizer unavailable
    """
    log_prefix = make_log_prefix(params)
    text = params.get("text")
    model = params.get("model")

    if not text or not isinstance(text, str):
        raise RPCError("INVALID_PARAMS", "text is required (string)")

    tokenizer_callback = get_tokenizer_callback()

    if tokenizer_callback is None:
        raise RPCError("TOKENIZER_UNAVAILABLE", "No tokenizer callback registered")

    try:
        result = await tokenizer_callback(text, model)
        return {"count": result["count"], "method": "exact"}
    except Exception as e:
        logger.error(f"{log_prefix} Tokenizer callback failed: {e}")
        raise RPCError("TOKENIZER_ERROR", f"Tokenization failed: {e}") from e
