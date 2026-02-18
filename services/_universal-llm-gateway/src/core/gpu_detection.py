"""GPU detection for platform-aware model filtering."""

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
    _llama_installed: bool | None = None
    _llama_gpu_available: bool | None = None
    _llama_backend: str | None = None
    _llama_server_available: bool | None = None
    _torch_gpu_available: bool | None = None
    _torch_backend: str | None = None
    _vllm_available: bool | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    @lru_cache(maxsize=1)
    def detect_llama(cls) -> tuple[bool, str | None]:
        """
        Detect if GPU acceleration is available for llama-cpp-python.

        Returns:
            Tuple of (gpu_available: bool, backend: str | None)
            backend can be: "CUDA", "Metal", "ROCm", "Vulkan", or None
        """
        if cls._llama_gpu_available is not None:
            return cls._llama_gpu_available, cls._llama_backend

        try:
            from llama_cpp import llama_cpp

            # llama-cpp-python is installed
            cls._llama_installed = True

            # Get system info from llama.cpp
            info_raw = llama_cpp.llama_print_system_info()
            # llama_print_system_info() can return bytes or str depending on version
            info = info_raw.decode() if hasattr(info_raw, "decode") else str(info_raw)

            info_upper = info.upper()

            # Detect GPU backend
            if "CUDA" in info_upper:
                cls._llama_gpu_available = True
                cls._llama_backend = "CUDA"
                logger.info("✅ llama-cpp-python GPU acceleration available: CUDA")
            elif "METAL" in info_upper:
                cls._llama_gpu_available = True
                cls._llama_backend = "Metal"
                logger.info(
                    "✅ llama-cpp-python GPU acceleration available: Metal (Apple Silicon)"
                )
            elif "ROCM" in info_upper or "HIP" in info_upper:
                cls._llama_gpu_available = True
                cls._llama_backend = "ROCm"
                logger.info(
                    "✅ llama-cpp-python GPU acceleration available: ROCm (AMD)"
                )
            elif "VULKAN" in info_upper:
                cls._llama_gpu_available = True
                cls._llama_backend = "Vulkan"
                logger.info("✅ llama-cpp-python GPU acceleration available: Vulkan")
            else:
                cls._llama_gpu_available = False
                cls._llama_backend = None
                logger.info(
                    "ℹ️ llama-cpp-python installed (CPU-only) - GGUF cpu_profiles supported"
                )

        except ImportError:
            cls._llama_installed = False
            cls._llama_gpu_available = False
            cls._llama_backend = None
            logger.warning(
                "⚠️ llama-cpp-python not installed - GGUF models not available"
            )
        except Exception as e:
            cls._llama_installed = False
            cls._llama_gpu_available = False
            cls._llama_backend = None
            logger.warning(
                f"⚠️ Failed to detect llama-cpp capabilities: {e} - GGUF models not available"
            )

        return cls._llama_gpu_available, cls._llama_backend

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
                        f"✅ PyTorch GPU acceleration available: ROCm (AMD) - {torch.cuda.device_count()} device(s)"
                    )
                else:
                    cls._torch_backend = "CUDA"
                    logger.info(
                        f"✅ PyTorch GPU acceleration available: CUDA - {torch.cuda.device_count()} device(s)"
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
        Detect if vLLM is installed and usable (requires both vLLM package and PyTorch GPU).

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
        Detect if any GPU acceleration is available (llama-cpp or torch).

        Returns:
            Tuple of (gpu_available: bool, backend: str | None)
            Returns the first available backend found.
        """
        # Check llama-cpp first (for GGUF models)
        llama_gpu, llama_backend = cls.detect_llama()
        if llama_gpu:
            return llama_gpu, llama_backend

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
    def is_llama_installed(cls) -> bool:
        """Check if llama-cpp-python is installed (required for any GGUF models)."""
        cls.detect_llama()  # Ensure detection has run
        return cls._llama_installed or False

    @classmethod
    def is_llama_gpu_available(cls) -> bool:
        """Check if GPU acceleration is available for llama-cpp-python (GGUF models)."""
        gpu_available, _ = cls.detect_llama()
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
    def is_torch_gpu_available(cls) -> bool:
        """Check if GPU acceleration is available for PyTorch."""
        gpu_available, _ = cls.detect_torch()
        return gpu_available

    @classmethod
    def is_vllm_available(cls) -> bool:
        """Check if vLLM is available (installed + PyTorch GPU) for HF/AWQ/GPTQ models."""
        return cls.detect_vllm()

    @classmethod
    def get_backend(cls) -> str | None:
        """Get the detected GPU backend name (first available)."""
        _, backend = cls.detect()
        return backend

    @classmethod
    def reset(cls):
        """Reset cached detection (useful for testing)."""
        cls._llama_installed = None
        cls._llama_gpu_available = None
        cls._llama_backend = None
        cls._llama_server_available = None
        cls._torch_gpu_available = None
        cls._torch_backend = None
        cls._vllm_available = None
        cls.detect_llama.cache_clear()
        cls.detect_llama_server.cache_clear()
        cls.detect_torch.cache_clear()
        cls.detect_vllm.cache_clear()
