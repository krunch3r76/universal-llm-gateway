"""GPU detection for platform-aware model filtering."""

import shutil
import subprocess
from functools import lru_cache

from universal_logging import get_logger

# NOTE: vLLM environment variables (VLLM_SLEEP_WHEN_IDLE, VLLM_ENABLE_INDUCTOR_*)
# are set in Dockerfile.gpu as ENV directives. They MUST be set at image level
# because vLLM uses multiprocessing.spawn which starts fresh Python interpreters
# that only inherit container-level env vars, not Python os.environ.setdefault().
# See Dockerfile.gpu "vLLM Runtime Environment Variables" section.

logger = get_logger(__name__)


class GPUCapabilities:
    """Detect and cache GPU/CUDA capabilities for llama-server and torch/vLLM."""

    _instance = None
    _llama_server_available: bool | None = None
    _hardware_gpu_available: bool | None = None
    _hardware_gpu_backend: str | None = None
    _torch_gpu_available: bool | None = None
    _torch_backend: str | None = None
    _vllm_available: bool | None = None

    def __new__(cls) -> "GPUCapabilities":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    @lru_cache(maxsize=1)
    def detect_torch(cls) -> tuple[bool, str | None]:
        """
        Detect if GPU acceleration is available for PyTorch/vLLM.

        Returns:
            Tuple of (gpu_available: bool, backend: str | None)
            backend can be: "CUDA", "ROCm", "MPS", or None
        """
        if cls._torch_gpu_available is not None:
            return cls._torch_gpu_available, cls._torch_backend

        try:
            import torch

            if torch.cuda.is_available():
                cls._torch_gpu_available = True
                # Check if it's ROCm (HIP) or NVIDIA CUDA
                if hasattr(torch.version, "hip") and torch.version.hip:
                    cls._torch_backend = "ROCm"
                    logger.info(
                        "✅ PyTorch GPU: ROCm (AMD) - %s device(s)",
                        torch.cuda.device_count(),
                    )
                else:
                    cls._torch_backend = "CUDA"
                    logger.info(
                        "✅ PyTorch GPU: CUDA - %s device(s)",
                        torch.cuda.device_count(),
                    )
            elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                cls._torch_gpu_available = True
                cls._torch_backend = "MPS"
                logger.info(
                    "✅ PyTorch GPU acceleration available: MPS (Apple Silicon)"
                )
            else:
                cls._torch_gpu_available = False
                cls._torch_backend = None
                logger.info("ℹ️ PyTorch compiled for CPU-only (no GPU backend)")

        except ImportError:
            cls._torch_gpu_available = False
            cls._torch_backend = None
            logger.warning(
                "⚠️ PyTorch not installed - assuming CPU-only for vLLM models"
            )
        except Exception as e:
            cls._torch_gpu_available = False
            cls._torch_backend = None
            logger.warning(
                f"⚠️ Failed to detect PyTorch GPU capabilities: {e} - assuming CPU-only"
            )

        return cls._torch_gpu_available, cls._torch_backend

    @classmethod
    @lru_cache(maxsize=1)
    def detect_vllm(cls) -> bool:
        """
        Detect if vLLM is installed and usable.

        Requires both the vLLM package and PyTorch GPU support.

        Returns:
            True if vLLM is installed and PyTorch has GPU support, False otherwise
        """
        if cls._vllm_available is not None:
            return cls._vllm_available

        # First check if PyTorch GPU is available
        torch_gpu, torch_backend = cls.detect_torch()
        if not torch_gpu:
            cls._vllm_available = False
            logger.info("ℹ️ vLLM not available - PyTorch has no GPU support")
            return cls._vllm_available

        # Then check if vLLM is installed
        try:
            import vllm

            cls._vllm_available = True
            version = getattr(vllm, "__version__", "unknown")
            logger.info(f"✅ vLLM {version} available with {torch_backend} backend")
        except ImportError:
            cls._vllm_available = False
            logger.info("ℹ️ vLLM not installed - HF/AWQ/GPTQ models not available")
        except Exception as e:
            cls._vllm_available = False
            logger.warning(f"⚠️ Failed to detect vLLM: {e}")

        return cls._vllm_available

    @classmethod
    def detect(cls) -> tuple[bool, str | None]:
        """
        Detect if any GPU acceleration is available (llama-server or torch).

        Returns:
            Tuple of (gpu_available: bool, backend: str | None)
            Returns the first available backend found.
        """
        # llama-server has no Python deps; validate binary + hardware
        if cls.is_llama_server_available():
            hardware_gpu, hardware_backend = cls.detect_hardware_gpu()
            if hardware_gpu:
                return hardware_gpu, hardware_backend

        # Check torch (for vLLM/HF models)
        torch_gpu, torch_backend = cls.detect_torch()
        if torch_gpu:
            return torch_gpu, torch_backend

        return False, None

    @classmethod
    def is_gpu_available(cls) -> bool:
        """Check if any GPU acceleration is available."""
        gpu_available, _ = cls.detect()
        return gpu_available

    @classmethod
    @lru_cache(maxsize=1)
    def detect_llama_server(cls) -> bool:
        """
        Detect if the llama-server binary is available.

        Returns:
            True if llama-server binary is found and executable
        """
        if cls._llama_server_available is not None:
            return cls._llama_server_available

        try:
            from inference_djinn.engines.gguf.native.binary import find_llama_server

            find_llama_server()
            cls._llama_server_available = True
            logger.info("✅ llama-server binary found - GGUF models supported")
        except FileNotFoundError:
            cls._llama_server_available = False
            logger.info("⚠️ llama-server binary not found - GGUF models not available")
        except Exception as e:
            cls._llama_server_available = False
            logger.warning(f"⚠️ Failed to detect llama-server: {e}")

        return cls._llama_server_available or False

    @classmethod
    def is_llama_server_available(cls) -> bool:
        """Check if llama-server binary is available (required for GGUF models)."""
        return cls.detect_llama_server()

    @classmethod
    @lru_cache(maxsize=1)
    def detect_hardware_gpu(cls) -> tuple[bool, str | None]:
        """
        Detect GPU at hardware/driver level (no Python package deps).

        Uses nvidia-smi to check for NVIDIA GPUs. Independent of torch
        or llama-cpp-python. Correct check for llama-server GPU
        capability since llama-server is a standalone binary.

        Returns:
            Tuple of (gpu_available: bool, backend: str | None)
        """
        if cls._hardware_gpu_available is not None:
            return cls._hardware_gpu_available, cls._hardware_gpu_backend

        if not shutil.which("nvidia-smi"):
            cls._hardware_gpu_available = False
            cls._hardware_gpu_backend = None
            logger.info("ℹ️ nvidia-smi not found - no NVIDIA GPU detected")
            return cls._hardware_gpu_available, cls._hardware_gpu_backend

        try:
            query = "--query-gpu=name,memory.total"
            result = subprocess.run(
                ["nvidia-smi", query, "--format=csv,noheader"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                gpu_info = result.stdout.strip().split("\n")[0]
                cls._hardware_gpu_available = True
                cls._hardware_gpu_backend = "CUDA"
                logger.info(f"✅ Hardware GPU detected (NVIDIA): {gpu_info}")
            else:
                cls._hardware_gpu_available = False
                cls._hardware_gpu_backend = None
                logger.info("ℹ️ nvidia-smi found but no usable GPU reported")
        except (subprocess.TimeoutExpired, OSError) as e:
            cls._hardware_gpu_available = False
            cls._hardware_gpu_backend = None
            logger.warning(f"⚠️ Failed to query nvidia-smi: {e}")

        return cls._hardware_gpu_available, cls._hardware_gpu_backend

    @classmethod
    def is_hardware_gpu_available(cls) -> bool:
        """Check if GPU hardware is available at the driver level."""
        gpu_available, _ = cls.detect_hardware_gpu()
        return gpu_available

    @classmethod
    def is_torch_gpu_available(cls) -> bool:
        """Check if GPU acceleration is available for PyTorch."""
        gpu_available, _ = cls.detect_torch()
        return gpu_available

    @classmethod
    def is_vllm_available(cls) -> bool:
        """Check if vLLM is available for HF/AWQ/GPTQ models (requires PyTorch GPU)."""
        return cls.detect_vllm()

    @classmethod
    def get_backend(cls) -> str | None:
        """Get the detected GPU backend name (first available)."""
        _, backend = cls.detect()
        return backend

    @classmethod
    def reset(cls) -> None:
        """Reset cached detection (useful for testing)."""
        cls._llama_server_available = None
        cls._hardware_gpu_available = None
        cls._hardware_gpu_backend = None
        cls._torch_gpu_available = None
        cls._torch_backend = None
        cls._vllm_available = None
        cls.detect_llama_server.cache_clear()
        cls.detect_hardware_gpu.cache_clear()
        cls.detect_torch.cache_clear()
        cls.detect_vllm.cache_clear()
