"""
vLLM engine for inference_djinn.

Uses vllm serve subprocess with OpenAI-compatible HTTP API (tool calling, streaming).
"""

from .inspector import get_vllm_model_info
from .server import VLLMServerEngine

__all__ = ["VLLMServerEngine", "get_vllm_model_info"]
