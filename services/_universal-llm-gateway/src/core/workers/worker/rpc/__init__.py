"""Worker RPC handlers (non-streaming)."""

from .embedding import EmbeddingHandlers
from .flux_image import FluxImageHandlers
from .inference import InferenceHandlers
from .lifecycle import LifecycleHandlers
from .load import LoadHandlers
from .metadata import MetadataHandlers
from .rerank import RerankHandlers
from .streams import StreamHandlers

__all__ = [
    "EmbeddingHandlers",
    "FluxImageHandlers",
    "InferenceHandlers",
    "LifecycleHandlers",
    "LoadHandlers",
    "MetadataHandlers",
    "RerankHandlers",
    "StreamHandlers",
]
