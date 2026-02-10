"""Build configuration management."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import psutil


class CPUMode(Enum):
    """CPU optimization mode for portable vs native builds.

    Note on AVX-512:
    - Intel 12th-14th gen CONSUMER CPUs (i9-13900K etc) disabled AVX-512
    - Intel SERVER CPUs (Ice Lake+, Sapphire Rapids) support AVX-512
    - AMD Zen 4+ (Ryzen 7000, EPYC) supports AVX-512
    - Use AVX512 only when targeting known-compatible hardware
    """

    NATIVE = (
        "native"  # -march=native -mtune=native (default, optimal for local machine)
    )
    AVX512 = "avx512"  # -march=x86-64-v4 (Intel server 2019+, AMD Zen4+, ~4-6x speedup)
    AVX2 = "avx2"  # -march=x86-64-v3 (portable: Intel 2013+, AMD 2015+, ~2-3x speedup)
    GENERIC = "generic"  # -march=x86-64 (maximum portability, baseline performance)


class GPUMode(Enum):
    """GPU optimization mode."""

    OPTIMIZED = "optimized"  # Single architecture (auto-detected)
    GENERIC = "generic"  # Multi-architecture
    CUSTOM = "custom"  # User-specified architecture


class JobsMode(Enum):
    """Build parallelization mode."""

    CONSERVATIVE = "conservative"  # nproc / 2 (default)
    MAXIMUM = "max"  # nproc - 1
    CUSTOM = "custom"  # User-specified


@dataclass
class BuildConfig:
    """Complete build configuration."""

    # CPU optimization
    cpu_mode: CPUMode = CPUMode.NATIVE

    # GPU optimization
    gpu_mode: GPUMode = GPUMode.OPTIMIZED
    gpu_arch: str | None = None  # e.g., "89", "120", "multi"

    # Build parallelization
    jobs_mode: JobsMode = JobsMode.CONSERVATIVE
    max_jobs: int | None = None

    # Paths
    repo_root: str = ""
    venv_dir: str = ""
    source_dir: str = ""

    # Flags
    skip_tests: bool = False
    verbose: bool = False
    force: bool = False  # Allow building lower CPU mode than machine supports

    def __post_init__(self):
        """Validate and compute derived values."""
        self._detect_max_jobs()
        self._detect_gpu_arch()
        self._validate_gpu_arch()
        self._validate_resources()

    def _validate_gpu_arch(self):
        """Validate GPU architecture format.

        GPU architectures must be at least 2 digits (e.g., "89", "120").
        Single digits like "8" are invalid and would cause string slicing errors.
        """
        if self.gpu_arch is None or self.gpu_arch == "multi":
            return

        if len(self.gpu_arch) < 2:
            raise ValueError(
                f"❌ Invalid GPU architecture: '{self.gpu_arch}'\n"
                f"   GPU architecture must be at least 2 digits (e.g., 89, 120).\n"
                f"   Common values:\n"
                f"     - 80: A100 (Ampere)\n"
                f"     - 86: RTX 30xx (Ampere)\n"
                f"     - 89: RTX 40xx (Ada Lovelace)\n"
                f"     - 90: H100 (Hopper)\n"
                f"     - 120: RTX 50xx (Blackwell)\n"
                f"   Use --gpu-generic for multi-architecture builds."
            )

        if not self.gpu_arch.isdigit():
            raise ValueError(
                f"❌ Invalid GPU architecture: '{self.gpu_arch}'\n"
                f"   GPU architecture must contain only digits (e.g., 89, 120)."
            )

    def _detect_max_jobs(self):
        """Detect optimal job count."""
        total_cpus = (
            psutil.cpu_count(logical=True) or 4
        )  # Default to 4 if detection fails

        if self.max_jobs is not None:
            # User specified custom job count
            return

        if self.jobs_mode == JobsMode.CONSERVATIVE:
            self.max_jobs = max(2, total_cpus // 2)
        elif self.jobs_mode == JobsMode.MAXIMUM:
            self.max_jobs = max(1, total_cpus - 1)
        else:
            # CUSTOM mode requires max_jobs to be set, fallback to conservative
            self.max_jobs = max(2, total_cpus // 2)

    def _detect_gpu_arch(self):
        """Detect GPU architecture if needed."""
        if self.gpu_mode == GPUMode.OPTIMIZED and self.gpu_arch is None:
            from .gpu_detector import GPUDetector

            detector = GPUDetector()
            self.gpu_arch = detector.detect()
        elif self.gpu_mode == GPUMode.GENERIC:
            self.gpu_arch = "multi"

    def _validate_resources(self):
        """Check if sufficient resources available."""
        from .system_checker import SystemChecker

        checker = SystemChecker()
        if self.max_jobs is not None:
            checker.validate_memory(self.max_jobs)
        checker.validate_disk_space()

    @property
    def cpu_flags(self) -> str:
        """Get CPU compiler flags based on optimization mode."""
        flags_map = {
            CPUMode.NATIVE: "-march=native -mtune=native",
            CPUMode.AVX512: "-march=x86-64-v4 -mtune=generic",  # AVX-512 (4-6x speedup)
            CPUMode.AVX2: "-march=x86-64-v3 -mtune=generic",  # AVX2 portable (2-3x speedup)
            CPUMode.GENERIC: "-march=x86-64 -mtune=generic",  # Maximum portability
        }
        return flags_map.get(self.cpu_mode) or flags_map[CPUMode.NATIVE]

    @property
    def cuda_architectures(self) -> str:
        """Get CUDA architectures for CMake."""
        if self.gpu_arch == "multi":
            # CUDA 13 generic: Ampere and newer for llama-cpp-python
            # Includes sm_80, sm_86, sm_87, sm_89, sm_90, sm_120 for maximum portability
            return "80;86;87;89;90;120"
        else:
            # Single architecture
            return self.gpu_arch

    @property
    def torch_cuda_arch_list(self) -> str:
        """Get CUDA architectures for PyTorch/vLLM."""
        if self.gpu_arch == "multi" or self.gpu_arch is None:
            # vLLM generic: Ampere and newer (sm 80+) for maximum portability
            # Includes sm_80, sm_86, sm_87, sm_89, sm_90, sm_120
            return "8.0 8.6 8.7 8.9 9.0 12.0"
        else:
            # Single architecture (format: X.Y) - for optimized builds
            # Validated in _validate_gpu_arch to be at least 2 digits
            major, minor = self._parse_gpu_arch()
            return f"{major}.{minor}"

    def _parse_gpu_arch(self) -> tuple[str, str]:
        """Parse GPU architecture into major.minor components.

        Returns:
            Tuple of (major, minor) version strings.

        Example:
            "89" -> ("8", "9")
            "120" -> ("12", "0")
        """
        if self.gpu_arch is None or len(self.gpu_arch) < 2:
            raise ValueError(f"Invalid GPU architecture: {self.gpu_arch}")
        return self.gpu_arch[:-1], self.gpu_arch[-1]

    def summary(self) -> str:
        """Get human-readable configuration summary."""
        cpu_desc = {
            CPUMode.NATIVE: "native (-march=native, optimal for this machine)",
            CPUMode.AVX512: "avx512 (-march=x86-64-v4, Intel server/AMD Zen4+)",
            CPUMode.AVX2: "avx2 (-march=x86-64-v3, portable for Docker)",
            CPUMode.GENERIC: "generic (-march=x86-64, maximum portability)",
        }
        max_jobs = self.max_jobs or 4
        lines = [
            "🔧 Build Configuration",
            f"  CPU: {cpu_desc.get(self.cpu_mode) or self.cpu_mode.value}",
            f"  GPU: {self.gpu_arch} ({self.gpu_mode.value})",
            f"  Jobs: {max_jobs} ({self.jobs_mode.value})",
            f"  Memory: ~{max_jobs * 3} GB required",
        ]
        return "\n".join(lines)
