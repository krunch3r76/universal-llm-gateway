"""
HTTP clients for OpenAI-compatible inference servers.

Shared by GGUF (llama-server) and vLLM server engines.
"""

from .openai_client import OpenAIServerClient

__all__ = ["OpenAIServerClient"]
