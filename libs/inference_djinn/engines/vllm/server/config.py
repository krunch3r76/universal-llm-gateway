"""
Configuration for vLLM server (vllm serve subprocess).

Contains VLLMServerConfig with CLI argument generation, environment setup,
and validation.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)


def detect_tool_call_parser(model_path: str) -> str:
    """Infer --tool-call-parser from model path.

    Parser names must match vLLM's supported set (v0.16+).
    Fallback: hermes (broad compatibility).

    Qwen3 (non-coder): hermes — model's default chat template produces
    JSON-inside-XML (<tool_call>{"name":...}</tool_call>), which the
    hermes parser handles via regex. The qwen3_xml parser expects full
    XML (<function=name><parameter=name>value</parameter></function>).
    """
    path_lower = model_path.lower()
    if "qwen3" in path_lower:
        if "coder" in path_lower:
            return "qwen3_coder"
        return "hermes"
    if "qwen2" in path_lower:
        return "hermes"
    if "llama-3" in path_lower or "llama3" in path_lower:
        return "llama3_json"
    if "mistral" in path_lower or "devstral" in path_lower:
        return "mistral"
    return "hermes"


_VALID_PARSERS = frozenset(
    {
        "deepseek_v3",
        "deepseek_v31",
        "deepseek_v32",
        "functiongemma",
        "glm45",
        "glm47",
        "granite",
        "granite-20b-fc",
        "hermes",
        "internlm",
        "jamba",
        "kimi_k2",
        "llama3_json",
        "llama4_json",
        "llama4_pythonic",
        "minimax",
        "minimax_m2",
        "mistral",
        "olmo3",
        "openai",
        "phi4_mini_json",
        "pythonic",
        "qwen3_coder",
        "qwen3_xml",
        "xlam",
    }
)


@dataclass(slots=True, kw_only=True)
class VLLMServerConfig:
    """Configuration for vllm serve instance.

    Structured fields cover parameters the engine manages directly.
    Additional catalog loader fields (enforce_eager, load_format, etc.)
    are passed through via extra_cli_args → CLI without interpretation.
    """

    model_path: str

    # Server binding
    host: str = "127.0.0.1"
    port: int = 8000
    socket_path: str | None = None

    # Tool calling
    enable_auto_tool_choice: bool = True
    tool_call_parser: str | None = None

    # Model / capacity
    max_model_len: int | None = None
    gpu_memory_utilization: float = 0.90
    quantization: str | None = None
    dtype: str = "auto"

    # Performance
    tensor_parallel_size: int = 1

    # Logging
    disable_log_requests: bool = True

    # Pass-through: catalog loader fields not modeled as explicit fields above.
    # Emitted as --key-name [value] in to_cli_args().
    extra_cli_args: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If configuration is invalid or violates security policy
            FileNotFoundError: If model path does not exist
        """
        path = Path(self.model_path)
        if not path.exists():
            raise FileNotFoundError(f"Model path not found: {self.model_path}")
        if not path.is_dir():
            raise FileNotFoundError(
                f"vLLM model path must be a directory: {self.model_path}"
            )
        if not self.socket_path and self.port <= 0:
            raise ValueError("When using TCP, port must be positive")
        if (
            self.tool_call_parser is not None
            and self.tool_call_parser not in _VALID_PARSERS
        ):
            msg = (
                f"tool_call_parser must be one of {sorted(_VALID_PARSERS)}, "
                f"got {self.tool_call_parser!r}"
            )
            raise ValueError(msg)
        # trust_remote_code=True executes arbitrary code from model repos.
        # All models are local; remote code execution is never permitted.
        if self.extra_cli_args.get("trust_remote_code") is True:
            raise ValueError(
                "trust_remote_code=True is forbidden: "
                "models are local and remote code execution is not permitted"
            )

    def to_subprocess_env(self) -> dict[str, str]:
        """Build subprocess environment for vllm serve.

        Enforces offline mode so vLLM never attempts network access
        (Edge container runs with network_mode: none).

        Sets RTX 5090 / Blackwell (SM_120) optimizations to match the
        measurement script (vllm_memory_test.py), ensuring identical
        attention backend and allocator behaviour at runtime.
        """
        env = dict(os.environ)
        # Offline: prevent HuggingFace / vLLM network access
        env["HF_HUB_OFFLINE"] = "1"
        env["TRANSFORMERS_OFFLINE"] = "1"
        env["VLLM_NO_USAGE_STATS"] = "1"
        env["HF_HUB_DISABLE_TELEMETRY"] = "1"
        # RTX 5090 / SM_120: Flash Attention 2 (not Triton) for compatibility
        env["VLLM_ATTENTION_BACKEND"] = "FLASH_ATTN"
        env["VLLM_FLASH_ATTN_VERSION"] = "2"
        env["VLLM_USE_TRITON_FLASH_ATTN"] = "0"
        # Reduce CUDA memory fragmentation
        env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
        return env

    def to_cli_args(self) -> list[str]:
        """Convert configuration to vllm serve CLI arguments.

        Structured fields are emitted first, then extra_cli_args are
        appended as --key-name [value] (bool True → flag, False → omit,
        other types → --key-name str(value)).

        Gateway-internal keys (e.g. warmup) must be stripped before
        populating extra_cli_args. Any dict/list values that reach this
        point are unexpected and logged at WARNING.
        """
        args = ["vllm", "serve", self.model_path]

        if self.socket_path:
            args.extend(["--uds", self.socket_path])
        else:
            args.extend(["--host", self.host])
            args.extend(["--port", str(self.port)])

        if self.enable_auto_tool_choice:
            args.append("--enable-auto-tool-choice")
        if self.tool_call_parser:
            args.extend(["--tool-call-parser", self.tool_call_parser])

        if self.max_model_len is not None:
            args.extend(["--max-model-len", str(self.max_model_len)])
        args.extend(["--gpu-memory-utilization", str(self.gpu_memory_utilization)])
        if self.quantization:
            args.extend(["--quantization", self.quantization])
        args.extend(["--dtype", self.dtype])

        if self.tensor_parallel_size != 1:
            args.extend(["--tensor-parallel-size", str(self.tensor_parallel_size)])

        if self.disable_log_requests:
            args.append("--disable-log-requests")

        # Pass-through catalog fields → CLI.
        # trust_remote_code is stripped as a second defence (validate() raises first).
        # Dict/list values indicate a gateway-internal key that was not stripped at
        # intake — log WARNING so the gap is visible rather than silently dropped.
        for key, value in self.extra_cli_args.items():
            if key == "trust_remote_code":
                continue
            if isinstance(value, dict | list):
                logger.warning(
                    "Skipping extra_cli_arg %r with complex value — "
                    "gateway-internal keys must be stripped before VLLMServerConfig",
                    key,
                )
                continue
            cli_flag = f"--{key.replace('_', '-')}"
            if isinstance(value, bool):
                if value:
                    args.append(cli_flag)
            else:
                args.extend([cli_flag, str(value)])

        return args


__all__ = ["VLLMServerConfig", "detect_tool_call_parser"]
