"""
Format-aware model replication policies.

Defines how many instances of a model can be loaded and where,
based on the model's inference engine format.

Policy Matrix:
    Format    | Per-Gateway Max | Multi-Gateway | Rationale
    ----------|-----------------|---------------|---------------------------
    gguf      | 1               | yes           | llama-cpp CUDA corruption when same model loaded 2x
    vllm      | 1               | yes           | Native batching
    exl3      | 1               | yes           | Native batching
    whisper   | unlimited       | yes           | Cheap CPU models

Note on GGUF:
    Two processes loading the same GGUF model file causes:
    1. Performance degradation (not 2x scaling)
    2. JSON output corruption under concurrent inference
    This is likely due to mmap/CUDA driver conflicts when the same model
    is accessed by multiple processes. Limiting to 1 instance prevents this.
"""

from dataclasses import dataclass
from enum import StrEnum


class ModelFormat(StrEnum):
    """Supported model formats with distinct replication strategies."""

    GGUF = "gguf"
    VLLM = "vllm"
    EXL3 = "exl3"
    WHISPER = "whisper"
    FLUX2 = "flux2"
    HF = "hf"  # Hugging Face (uses vLLM)
    AWQ = "awq"  # AWQ quantized (uses vLLM)
    GPTQ = "gptq"  # GPTQ quantized (uses vLLM)


@dataclass(frozen=True, slots=True)
class ReplicationPolicy:
    """
    Replication rules for a model format.

    Attributes:
        format: Model format identifier
        max_instances_per_gateway: Max instances on single gateway (0 = unlimited)
        allow_multi_gateway: Whether instances can span multiple gateways
        supports_batching: Whether engine supports request batching
    """

    format: ModelFormat
    max_instances_per_gateway: int  # 0 = unlimited
    allow_multi_gateway: bool
    supports_batching: bool

    def can_add_instance(self, current_count: int) -> bool:
        """Check if another instance can be added to a gateway."""
        if self.max_instances_per_gateway == 0:
            return True  # Unlimited
        return current_count < self.max_instances_per_gateway


# Default policies by format
REPLICATION_POLICIES: dict[str, ReplicationPolicy] = {
    ModelFormat.GGUF.value: ReplicationPolicy(
        format=ModelFormat.GGUF,
        max_instances_per_gateway=1,  # Single - llama-cpp CUDA corruption under concurrency
        allow_multi_gateway=True,
        supports_batching=False,
    ),
    ModelFormat.VLLM.value: ReplicationPolicy(
        format=ModelFormat.VLLM,
        max_instances_per_gateway=1,  # Single - has batching
        allow_multi_gateway=True,  # Multi-GPU scale-out
        supports_batching=True,
    ),
    ModelFormat.EXL3.value: ReplicationPolicy(
        format=ModelFormat.EXL3,
        max_instances_per_gateway=1,  # Single - has batching
        allow_multi_gateway=True,
        supports_batching=True,
    ),
    ModelFormat.WHISPER.value: ReplicationPolicy(
        format=ModelFormat.WHISPER,
        max_instances_per_gateway=0,  # Unlimited - cheap CPU
        allow_multi_gateway=True,
        supports_batching=False,
    ),
    ModelFormat.FLUX2.value: ReplicationPolicy(
        format=ModelFormat.FLUX2,
        max_instances_per_gateway=1,  # 32B params - VRAM intensive
        allow_multi_gateway=True,
        supports_batching=False,
    ),
    # vLLM-based formats
    ModelFormat.HF.value: ReplicationPolicy(
        format=ModelFormat.HF,
        max_instances_per_gateway=1,
        allow_multi_gateway=True,
        supports_batching=True,
    ),
    ModelFormat.AWQ.value: ReplicationPolicy(
        format=ModelFormat.AWQ,
        max_instances_per_gateway=1,
        allow_multi_gateway=True,
        supports_batching=True,
    ),
    ModelFormat.GPTQ.value: ReplicationPolicy(
        format=ModelFormat.GPTQ,
        max_instances_per_gateway=1,
        allow_multi_gateway=True,
        supports_batching=True,
    ),
}


class UnknownFormatError(ValueError):
    """Raised when replication policy requested for unknown format."""


def get_replication_policy(format_str: str) -> ReplicationPolicy:
    """
    Get replication policy for a model format.

    Args:
        format_str: Format string from model metadata (e.g., "gguf", "vllm")

    Returns:
        ReplicationPolicy for the format

    Raises:
        UnknownFormatError: If format is unknown (no fallback)
    """
    if not format_str:
        raise UnknownFormatError("Model format string is empty or None")

    normalized_format = format_str.lower()
    policy = REPLICATION_POLICIES.get(normalized_format)
    if policy is None:
        valid_formats = ", ".join(sorted(REPLICATION_POLICIES.keys()))
        raise UnknownFormatError(
            f"Unknown model format '{format_str}'. Valid formats: {valid_formats}"
        )
    return policy


def format_supports_multi_instance_per_gateway(format_str: str) -> bool:
    """
    Quick check if format allows multiple instances per gateway.

    Args:
        format_str: Format string from model metadata

    Returns:
        True if format allows > 1 instance per gateway
    """
    policy = get_replication_policy(format_str)
    return policy.max_instances_per_gateway == 0 or policy.max_instances_per_gateway > 1
