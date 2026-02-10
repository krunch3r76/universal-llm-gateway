"""vLLM builder module."""

from .builder import VLLMBuilder

# Patch workflows are imported lazily when needed
# to avoid circular import issues with dataclasses
__all__ = ["VLLMBuilder"]
