"""Factory for creating vision model chat handlers."""

from typing import Any

from universal_logging import get_logger

from .registry import get_vision_model_info

logger = get_logger(__name__)


def _wrap_handler_with_logging(handler: Any, clip_model_path: str) -> Any:
    """
    Wrap vision handler to log CLIP model loading explicitly.

    Args:
        handler: The vision handler instance
        clip_model_path: Path to CLIP model (for logging)

    Returns:
        Wrapped handler that logs CLIP loading
    """
    original_call = handler.__call__

    def wrapped_call(self, **kwargs):
        # Check if this is the first call (CLIP not loaded yet)
        if hasattr(self, "mtmd_ctx") and self.mtmd_ctx is None:
            logger.info(f"🔮 Loading CLIP model from: {clip_model_path}")
            try:
                result = original_call(self, **kwargs)
                # If we got here, CLIP loaded successfully
                if self.mtmd_ctx is not None:
                    logger.info(f"✅ CLIP model loaded successfully: {clip_model_path}")
                else:
                    logger.error(f"❌ CLIP model failed to load: {clip_model_path}")
                    raise RuntimeError(f"CLIP model failed to load: {clip_model_path}")
                return result
            except Exception as e:
                logger.error(f"❌ Exception during CLIP model loading: {e}")
                raise
        else:
            # CLIP already loaded, just call normally
            return original_call(self, **kwargs)

    handler.__call__ = wrapped_call
    return handler


def create_vision_handler(
    vision_architecture: str,
    clip_model_path: str,
    verbose: bool = True,
) -> Any:
    """
    Create the appropriate chat handler for a vision model.

    Args:
        vision_architecture: Key from VISION_MODEL_REGISTRY
        clip_model_path: Path to CLIP/mmproj model file
        verbose: Enable verbose logging from llama.cpp for image processing

    Returns:
        Instantiated chat handler ready for Llama() constructor

    Raises:
        ValueError: If architecture unknown or handler import fails
    """
    model_info = get_vision_model_info(vision_architecture)
    if not model_info:
        raise ValueError(f"Unknown vision architecture: {vision_architecture}")

    handler_class_name = model_info.handler_class_name

    try:
        # Dynamic import from llama_cpp.llama_chat_format
        from llama_cpp import llama_chat_format

        handler_class = getattr(llama_chat_format, handler_class_name, None)
        if handler_class is None:
            raise ValueError(
                f"Handler class {handler_class_name} not found in llama_cpp.llama_chat_format. "
                f"Ensure llama-cpp-python version supports this model."
            )

        logger.info(f"Creating vision handler: {handler_class_name}")
        logger.info(f"  CLIP model: {clip_model_path}")
        logger.info(f"  Verbose: {verbose}")

        # All vision handlers take clip_model_path and verbose as constructor args
        handler = handler_class(clip_model_path=clip_model_path, verbose=verbose)

        logger.info(f"✅ Vision handler created: {handler_class_name}")
        logger.info("⚠️  NOTE: CLIP model will be loaded on first inference with images")

        # Wrap handler to log CLIP loading explicitly
        handler = _wrap_handler_with_logging(handler, clip_model_path)

        return handler

    except ImportError as e:
        raise ValueError(
            f"Failed to import llama_cpp.llama_chat_format: {e}. "
            "Ensure llama-cpp-python is installed."
        ) from e
    except Exception as e:
        raise ValueError(f"Failed to create handler {handler_class_name}: {e}") from e


def get_recommended_n_ctx(
    vision_architecture: str, base_n_ctx: int | None = None
) -> int:
    """
    Get recommended n_ctx for a vision model.

    Vision models need larger context to accommodate image embeddings.

    Args:
        vision_architecture: Key from VISION_MODEL_REGISTRY
        base_n_ctx: User-specified n_ctx (if any)

    Returns:
        Recommended n_ctx value
    """
    model_info = get_vision_model_info(vision_architecture)
    if not model_info:
        return base_n_ctx or 4096

    default_n_ctx = model_info.default_n_ctx

    if base_n_ctx is None:
        return default_n_ctx

    # Warn if user-specified n_ctx is smaller than recommended
    if base_n_ctx < default_n_ctx:
        logger.warning(
            f"⚠️ n_ctx={base_n_ctx} may be too small for {vision_architecture}. "
            f"Recommended: {default_n_ctx}. Image embeddings require significant context."
        )

    return base_n_ctx
