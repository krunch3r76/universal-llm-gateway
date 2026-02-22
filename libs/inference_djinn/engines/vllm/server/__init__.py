"""
vLLM server engine (subprocess + OpenAI-compatible HTTP API).

Replaces the library-based VLLMEngine with a vllm serve subprocess pattern
matching the GGUF native engine, enabling tool calling passthrough.
"""

from .config import VLLMServerConfig
from .engine import VLLMServerEngine
from .manager import VLLMServerManager

__all__ = ["VLLMServerConfig", "VLLMServerEngine", "VLLMServerManager"]
