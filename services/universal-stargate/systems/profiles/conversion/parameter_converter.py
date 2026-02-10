"""
Parameter converter - converts generation parameters between inference engines.

Handles conversion from llama-cpp format (base/reference) to vLLM and other engines.
"""

from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


class ParameterConverter:
    """Converts generation parameters between different inference engines."""

    # Shared parameters that work across all engines (no conversion needed)
    SHARED_PARAMS = {
        "temperature",
        "top_p",
        "top_k",
        "max_tokens",
        "stop",
        "seed",
        "stream",
        "n",
    }

    # llama.cpp specific parameters that need conversion or filtering
    LLAMA_CPP_SPECIFIC = {
        "repeat_penalty",
        "min_p",
        "typical_p",
        "tfs_z",
        "mirostat_mode",
        "mirostat_tau",
        "mirostat_eta",
    }

    # vLLM specific parameters
    VLLM_SPECIFIC = {
        "presence_penalty",
        "frequency_penalty",
        "best_of",
        "use_beam_search",
        "length_penalty",
        "early_stopping",
        "ignore_eos",
        "logprobs",
        "skip_special_tokens",
    }

    @staticmethod
    def convert_llama_cpp_to_vllm(params: dict[str, Any]) -> dict[str, Any]:
        """
        Convert llama-cpp parameters to vLLM-compatible parameters.

        Conversion rules:
        - repeat_penalty → presence_penalty (subtract 1.0)
        - Filter out unsupported params (min_p, typical_p, tfs_z, mirostat_*)
        - Keep shared params unchanged
        """
        converted = {}

        for key, value in params.items():
            if key in ParameterConverter.SHARED_PARAMS:
                converted[key] = value
            elif key == "repeat_penalty" and value is not None:
                presence = float(value) - 1.0
                if presence > 0:
                    converted["presence_penalty"] = presence
                    logger.debug(
                        f"Converted repeat_penalty={value} "
                        f"to presence_penalty={presence}"
                    )
            elif key in ParameterConverter.LLAMA_CPP_SPECIFIC:
                logger.debug(f"Filtered out llama-cpp specific param: {key}={value}")
            elif key in ParameterConverter.VLLM_SPECIFIC:
                converted[key] = value
            else:
                converted[key] = value
                logger.debug(f"Passed through unknown param: {key}={value}")

        return converted

    @staticmethod
    def convert_vllm_to_llama_cpp(params: dict[str, Any]) -> dict[str, Any]:
        """
        Convert vLLM parameters to llama-cpp-compatible parameters.

        Reverse conversion for validation or bidirectional support.
        """
        converted = {}

        for key, value in params.items():
            if key in ParameterConverter.SHARED_PARAMS:
                converted[key] = value
            elif key == "presence_penalty" and value is not None:
                repeat = float(value) + 1.0
                converted["repeat_penalty"] = repeat
                logger.debug(
                    f"Converted presence_penalty={value} to repeat_penalty={repeat}"
                )
            elif key == "frequency_penalty" and value is not None:
                if "repeat_penalty" not in converted:
                    repeat = float(value) + 1.0
                    converted["repeat_penalty"] = repeat
                    logger.debug(
                        f"Converted frequency_penalty={value} "
                        f"to repeat_penalty={repeat}"
                    )
            elif key in ParameterConverter.VLLM_SPECIFIC:
                logger.debug(f"Filtered out vLLM specific param: {key}={value}")
            elif key in ParameterConverter.LLAMA_CPP_SPECIFIC:
                converted[key] = value
            else:
                converted[key] = value
                logger.debug(f"Passed through unknown param: {key}={value}")

        return converted

    @staticmethod
    def filter_for_engine(params: dict[str, Any], engine: str) -> dict[str, Any]:
        """Filter parameters to only those supported by the target engine."""
        if engine == "llama_cpp":
            allowed = (
                ParameterConverter.SHARED_PARAMS | ParameterConverter.LLAMA_CPP_SPECIFIC
            )
        elif engine == "vllm":
            allowed = (
                ParameterConverter.SHARED_PARAMS | ParameterConverter.VLLM_SPECIFIC
            )
        else:
            logger.warning(f"Unknown engine '{engine}', allowing all parameters")
            return params.copy()

        filtered = {k: v for k, v in params.items() if k in allowed}
        removed = set(params.keys()) - set(filtered.keys())

        if removed:
            logger.debug(f"Filtered out unsupported params for {engine}: {removed}")

        return filtered
