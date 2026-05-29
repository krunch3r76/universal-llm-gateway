"""Abstract base for the pipeline error hierarchy.

Defines :class:`PipelineError`, the root of every structured pipeline
validation/runtime error. Concrete subclasses live in sibling modules of the
``errors`` package and are re-exported from its ``__init__``. Every concrete
error implements ``to_dict()`` returning a JSON-compatible dict for API
response envelopes, and exposes a ``retryable`` flag defaulting to ``False``.
"""

from abc import ABC, abstractmethod


class PipelineError(RuntimeError, ABC):
    """Base class for pipeline validation/runtime errors."""

    @property
    def retryable(self) -> bool:
        """Whether this error represents a transient condition worth retrying."""
        return False

    @abstractmethod
    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        ...
