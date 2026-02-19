"""
Configuration for native llama-server integration.

Contains ServerConfig (CLI argument generation, validation) and
APIFormat enum. Separated from server.py for SLOC compliance.
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class APIFormat(StrEnum):
    """Supported API formats for llama-server."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"


@dataclass(slots=True, kw_only=True)
class ServerConfig:
    """Configuration for llama-server instance."""

    # Model configuration (single-model mode)
    model_path: str | None = None

    # Router mode configuration (multi-model mode)
    models_dir: str | None = None
    models_max: int = 4
    no_models_autoload: bool = False

    # Server configuration
    host: str = "127.0.0.1"
    port: int = 8080
    timeout: int = 600

    # Unix socket (preferred over TCP — eliminates port conflicts)
    socket_path: str | None = None

    # Parallel processing (1 unless catalog configures higher)
    parallel_slots: int = 1
    continuous_batching: bool = Trueg

    # Context configuration
    ctx_size: int = 8192
    n_gpu_layers: int = -1

    # CPU threading (¬set → falls back to LLAMA_ARG_THREADS env var)
    n_threads: int | None = None  # Generation threads
    n_threads_batch: int | None = None  # Batch/prompt processing threads

    # API format
    api_format: APIFormat = APIFormat.OPENAI

    # Batch size (-b)
    batch_size: int = 512

    # KV cache types (-ctk/-ctv; f16 when f16_kv=True, f32 when False)
    cache_type_k: str = "f16"
    cache_type_v: str = "f16"

    # Advanced options
    flash_attn: bool = True
    no_mmap: bool = False
    mlock: bool = True
    numa: bool = False

    # Vision models
    mmproj_path: str | None = None

    # Embedding mode
    # TRICKY: --embedding makes llama-server embedding-ONLY (¬chat/completions)
    # Each worker loads one model, so mutual exclusivity is fine.
    embedding: bool = False
    pooling: str | None = None  # {none, mean, cls, last, rank}
    ubatch_size: int | None = None  # -ub, recommended 8192 for embeddings

    # Logging
    verbose: bool = False

    def validate(self) -> None:
        """Validate configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        if self.model_path and self.models_dir:
            raise ValueError("Specify either model_path OR models_dir, not both")

        if not self.model_path and not self.models_dir:
            raise ValueError("Must specify model_path OR models_dir")

        if self.model_path:
            path = Path(self.model_path)
            if not path.exists():
                raise FileNotFoundError(f"Model not found: {self.model_path}")

        if self.models_dir:
            path = Path(self.models_dir)
            if not path.exists():
                raise FileNotFoundError(
                    f"Models directory not found: {self.models_dir}"
                )

        if self.mmproj_path:
            path = Path(self.mmproj_path)
            if not path.exists():
                raise FileNotFoundError(f"MMProj file not found: {self.mmproj_path}")

        # Unix socket validation: llama-server requires .sock extension
        # See: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
        if self.socket_path and not self.socket_path.endswith(".sock"):
            raise ValueError(
                f"Unix socket path must end with .sock for llama-server: {self.socket_path}"
            )

        valid_poolings = {"none", "mean", "cls", "last", "rank"}
        if self.pooling and self.pooling not in valid_poolings:
            raise ValueError(
                f"Invalid pooling type: {self.pooling}. "
                f"Must be one of: {valid_poolings}"
            )

    def to_cli_args(self) -> list[str]:
        """Convert configuration to llama-server CLI arguments."""
        args = ["llama-server"]

        # Model configuration
        if self.model_path:
            args.extend(["-m", self.model_path])
        elif self.models_dir:
            args.extend(["--models-dir", self.models_dir])
            args.extend(["--models-max", str(self.models_max)])
            if self.no_models_autoload:
                args.append("--no-models-autoload")

        # Server configuration — prefer Unix socket over TCP
        # TRICKY: llama-server's --host accepts Unix socket paths ending in .sock
        # See: https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md
        if self.socket_path:
            args.extend(["--host", self.socket_path])
        else:
            args.extend(["--host", self.host])
            args.extend(["--port", str(self.port)])

        # Parallel processing
        args.extend(["-np", str(self.parallel_slots)])
        if self.continuous_batching:
            args.append("-cb")

        # Context and batch configuration
        args.extend(["-c", str(self.ctx_size)])
        args.extend(["-ngl", str(self.n_gpu_layers)])
        args.extend(["-b", str(self.batch_size)])

        # KV cache types (only emit when non-default to keep CLI clean)
        if self.cache_type_k != "f16":
            args.extend(["-ctk", self.cache_type_k])
        if self.cache_type_v != "f16":
            args.extend(["-ctv", self.cache_type_v])

        # CPU threading (only if explicitly set; otherwise LLAMA_ARG_THREADS env var used)
        if self.n_threads is not None:
            args.extend(["--threads", str(self.n_threads)])
        if self.n_threads_batch is not None:
            args.extend(["--threads-batch", str(self.n_threads_batch)])

        # Advanced options
        if self.flash_attn:
            args.extend(["--flash-attn", "on"])
        if self.no_mmap:
            args.append("--no-mmap")
        if self.mlock:
            args.append("--mlock")
        if self.numa:
            args.append("--numa")

        # Vision models
        if self.mmproj_path:
            args.extend(["--mmproj", self.mmproj_path])

        # Embedding mode
        # TRICKY: --embedding restricts server to embedding-only mode
        # ¬compatible with chat/completions endpoints
        if self.embedding:
            args.append("--embedding")
        if self.pooling:
            args.extend(["--pooling", self.pooling])
        if self.ubatch_size is not None:
            args.extend(["-ub", str(self.ubatch_size)])

        # Logging
        if self.verbose:
            args.append("--verbose")

        return args
