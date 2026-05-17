"""
Model summary orchestration for GGUF inspector.

Orchestrates the complete GGUF inspection summary and structured logging output.
"""

import pprint
from typing import Any

from universal_logging import get_logger

from .chat_template_analysis import detect_chat_template_support
from .metadata_loading import load_gguf_metadata
from .model_capabilities import analyze_model_capabilities
from .model_requirements import validate_model_requirements
from .model_type_detection import detect_model_type_from_metadata

logger = get_logger(__name__)


def get_model_info_summary(model_path: str) -> dict[str, Any]:
    """
    Get complete model information summary for GGUF model.

    Args:
        model_path: Path to the model file

    Returns:
        Complete model information dictionary
    """
    logger.debug("\033[94mLoading GGUF metadata\033[0m")
    meta = load_gguf_metadata(model_path)

    logger.debug("\033[94mPreparing caller info string\033[0m")
    caller_info = f"{__file__}:{get_model_info_summary.__name__}"

    logger.debug("\033[94mGGUF Inspector function call location logged\033[0m")

    logger.debug("\033[94mDetecting model type from metadata\033[0m")
    model_type = detect_model_type_from_metadata(meta) if meta else "unknown"

    logger.debug("\033[94mAnalyzing model capabilities\033[0m")
    capabilities = analyze_model_capabilities(model_path, meta)

    logger.debug("\033[94mDetecting chat template support\033[0m")
    chat_template = detect_chat_template_support(model_path, meta)

    logger.debug("\033[94mValidating model requirements\033[0m")
    validation = validate_model_requirements(model_path, meta)

    logger.debug("\033[94mAssembling model info dictionary\033[0m")
    info = {
        "model_path": model_path,
        "model_type": model_type,
        "capabilities": capabilities,
        "chat_template": chat_template,
        "validation": validation,
        "metadata": meta.to_dict() if meta else None,
        "engine": "gguf",
    }

    # Log the complete info summary
    logger.info(
        f"📊 Complete Model Info Summary ({caller_info}):\n{pprint.pformat(info)}"
    )

    if meta:
        logger.info(
            f"📝 GGUF Metadata (dict, {caller_info}):\n{pprint.pformat(meta.to_dict())}"
        )
    else:
        logger.info(f"📝 GGUF Metadata (dict, {caller_info}): None")

    return info
