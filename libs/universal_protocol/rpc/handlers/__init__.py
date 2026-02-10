"""RPC method handlers for Universal Protocol.

Implements RPC methods:
1. load_model - Load a model into memory
2. unload_model - Unload a model from memory
3. health - Check worker health and loaded models
4. count_tokens - Tokenize text
5. cancel_inference - Cancel an active inference stream
6. debug_stats - Return debug statistics

Note: start_inference is NOT implemented here. Workers provide their own.
"""

from .cancel_inference import handle_cancel_inference
from .count_tokens import handle_count_tokens
from .debug_stats import handle_debug_stats
from .health import handle_health
from .load_model import handle_load_model
from .model_state import LOADED_MODELS
from .tokenizer_hook import register_tokenizer_callback
from .unload_model import handle_unload_model

__all__ = [
    "handle_load_model",
    "handle_unload_model",
    "handle_health",
    "handle_count_tokens",
    "handle_cancel_inference",
    "handle_debug_stats",
    "LOADED_MODELS",
    "register_tokenizer_callback",
]
