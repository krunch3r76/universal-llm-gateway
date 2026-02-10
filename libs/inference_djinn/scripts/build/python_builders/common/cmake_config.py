"""
Unified CMAKE configuration generator for llama-cpp-python builds.

This module provides consistent CMAKE_ARGS generation for both:
- Docker builds (run as CLI tool)
- Python builders (imported as module)

Usage:
    # As CLI tool (Docker):
    python cmake_config.py --output-env
    python cmake_config.py --cpu-mode=auto --gpu-arch=multi --output-json

    # As module (Python builders):
    from common.cmake_config import CMakeConfigGenerator
    generator = CMakeConfigGenerator(cpu_mode="auto", gpu_arch="120")
    cmake_args = generator.get_cmake_args()

IMPORTANT: build_llama_server.sh duplicates the CPU/CUDA flag mappings
from this module. If you change flags here, also update:
  docker/scripts/build/build_llama_server.sh
Search for "Match cmake_config.py" comments in that file.

The duplication exists because:
  - llama-server builds in a minimal Docker context without Python deps
  - llama-server is a separate git clone (latest main), not a pip package
  - This module is designed for llama-cpp-python's scikit-build-core pipeline
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import sys
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)

# Exit codes for CI integration
EXIT_OK = 0
EXIT_CPU_MODE_MISMATCH = 2  # Explicit CPU mode below host capability


class CPUOptimization(Enum):
    """CPU optimization levels for SIMD instructions."""

    NATIVE = "native"  # -march=native: Maximum performance for build machine
    AVX512 = "avx512"  # x86-64-v4: AVX-512, VNNI, VBMI (4-6x faster)
    AVX2 = "avx2"  # x86-64-v3: AVX2, FMA, F16C (2-3x faster)
    GENERIC = "generic"  # x86-64: baseline (maximum portability)
    AUTO = "auto"  # Auto-detect instruction set (but use generic tuning)


@dataclass
class CPUCapabilities:
    """Detected CPU capabilities."""

    has_avx512: bool = False
    has_avx512_vnni: bool = False
    has_avx512_vbmi: bool = False
    has_avx2: bool = False
    has_fma: bool = False
    has_f16c: bool = False
    model_name: str = "Unknown"
    architecture: str = "x86-64"

    @classmethod
    def detect(cls) -> CPUCapabilities:
        """Auto-detect CPU capabilities from /proc/cpuinfo."""
        caps = cls()

        # Check platform
        machine = platform.machine().lower()
        if machine not in ("x86_64", "amd64"):
            logger.warning(f"Non-x86_64 architecture: {machine}")
            return caps

        try:
            with open("/proc/cpuinfo") as f:
                cpuinfo = f.read().lower()

            caps.has_avx512 = "avx512f" in cpuinfo or "avx512" in cpuinfo
            caps.has_avx512_vnni = "avx512_vnni" in cpuinfo or "avx512vnni" in cpuinfo
            caps.has_avx512_vbmi = "avx512_vbmi" in cpuinfo or "avx512vbmi" in cpuinfo
            caps.has_avx2 = "avx2" in cpuinfo
            caps.has_fma = "fma" in cpuinfo
            caps.has_f16c = "f16c" in cpuinfo

            # Get model name
            for line in cpuinfo.split("\n"):
                if line.startswith("model name"):
                    caps.model_name = line.split(":", 1)[1].strip()
                    break

            # Determine architecture level
            if caps.has_avx512:
                caps.architecture = "x86-64-v4"
            elif caps.has_avx2:
                caps.architecture = "x86-64-v3"
            else:
                caps.architecture = "x86-64"

        except Exception as e:
            logger.warning(f"Failed to detect CPU capabilities: {e}")

        return caps

    def recommended_optimization(self) -> CPUOptimization:
        """Get recommended CPU optimization based on capabilities."""
        if self.has_avx512:
            return CPUOptimization.AVX512
        elif self.has_avx2:
            return CPUOptimization.AVX2
        else:
            return CPUOptimization.GENERIC


@dataclass
class CMakeConfigGenerator:
    """
    Generate CMAKE_ARGS for llama-cpp-python builds.

    Provides consistent configuration for both Docker and local builds.

    Default: AVX2 (x86-64-v3) for portable Docker images.
    Use cpu_mode="auto" for local/native builds optimized for the current machine.
    """

    cpu_mode: str = "avx2"  # avx2 (default/portable), auto, avx512, generic
    gpu_arch: str = "multi"  # multi, or specific arch like "120", "89"
    cuda_enabled: bool = True
    # BLAS disabled by default: OpenBLAS threading conflicts with llama.cpp's OpenMP,
    # causing severe performance degradation in hybrid GPU+CPU inference workloads
    blas_enabled: bool = False
    build_tests: bool = False
    build_examples: bool = False
    force: bool = False  # Allow building lower CPU mode than machine supports

    # Derived values (set in __post_init__)
    cpu_optimization: CPUOptimization = field(init=False)
    cpu_capabilities: CPUCapabilities = field(init=False)

    def __post_init__(self):
        """Initialize derived values."""
        self.cpu_capabilities = CPUCapabilities.detect()

        # Resolve CPU mode
        if self.cpu_mode == "native":
            # True native: -march=native -mtune=native with aggressive optimizations
            self.cpu_optimization = CPUOptimization.NATIVE
            features = []
            if self.cpu_capabilities.has_avx512:
                features.append("AVX-512")
            if self.cpu_capabilities.has_avx2:
                features.append("AVX2")
            if self.cpu_capabilities.has_fma:
                features.append("FMA")
            logger.info(
                f"Native build: {self.cpu_capabilities.model_name} "
                f"({', '.join(features) if features else 'baseline'})"
            )
        elif self.cpu_mode == "auto":
            self.cpu_optimization = self.cpu_capabilities.recommended_optimization()
            logger.info(
                f"Auto-detected CPU: {self.cpu_capabilities.model_name} "
                f"-> {self.cpu_optimization.value}"
            )
        else:
            try:
                self.cpu_optimization = CPUOptimization(self.cpu_mode)
            except ValueError:
                logger.warning(
                    f"Unknown CPU mode '{self.cpu_mode}', defaulting to avx2"
                )
                self.cpu_optimization = CPUOptimization.AVX2

        # Validate CPU mode against build machine capabilities
        # 
        # IMPORTANT: llama.cpp uses runtime SIMD detection regardless of build flags.
        # The -march flag only affects compiler optimization, not which SIMD paths exist.
        # Therefore, building with --cpu-avx2 on an AVX-512 machine gives worst of both:
        # - Compiler uses -march=x86-64-v3 (suboptimal code generation)
        # - Runtime still uses AVX-512 (but code wasn't optimized for it)
        #
        # We enforce: explicit CPU mode must match or exceed build machine capabilities.
        # Use --cpu-native to auto-detect, or match the flag to your target deployment.
        
        if self.cpu_mode == "native":
            # Native mode: always valid, uses -march=native
            pass
        elif self.cpu_mode == "auto":
            # Auto mode: detect and use best available
            if (
                self.cpu_optimization == CPUOptimization.AVX512
                and not self.cpu_capabilities.has_avx512
            ):
                logger.warning(
                    "⚠️  WARNING: AVX-512 detected but not supported by current CPU.\n"
                    "   Falling back to AVX2."
                )
                self.cpu_optimization = CPUOptimization.AVX2

            if (
                self.cpu_optimization == CPUOptimization.AVX2
                and not self.cpu_capabilities.has_avx2
            ):
                logger.warning(
                    "⚠️  WARNING: AVX2 not supported by current CPU.\n"
                    "   Falling back to generic."
                )
                self.cpu_optimization = CPUOptimization.GENERIC
        else:
            # Explicit mode: user knows what they want, respect their choice
            # Log info if they're building with lower optimization than available
            machine_level = self.cpu_capabilities.recommended_optimization()
            if machine_level == CPUOptimization.AVX512 and self.cpu_optimization == CPUOptimization.AVX2:
                logger.info(
                    "ℹ️  Building with AVX2 on AVX-512 capable machine (intentional).\n"
                    "   This is useful for portable builds, Docker deployment, or workload-specific optimization."
                )
            elif machine_level == CPUOptimization.AVX512 and self.cpu_optimization == CPUOptimization.GENERIC:
                logger.info(
                    "ℹ️  Building with generic x86-64 on AVX-512 capable machine (maximum portability)."
                )
            elif machine_level == CPUOptimization.AVX2 and self.cpu_optimization == CPUOptimization.GENERIC:
                logger.info(
                    "ℹ️  Building with generic x86-64 on AVX2 capable machine (maximum portability)."
                )

    def _validate_cpu_mode_matches_machine(self):
        """
        Validate that explicit CPU mode matches build machine capabilities.
        
        Since llama.cpp uses runtime SIMD detection, building with a lower
        optimization level than the machine supports is counterproductive:
        - Compiler generates suboptimal code for the lower -march target
        - Runtime still uses higher SIMD paths (but code wasn't optimized for them)
        
        Raises:
            SystemExit: If CPU mode is lower than build machine capabilities.
        """
        machine_level = self.cpu_capabilities.recommended_optimization()
        requested_level = self.cpu_optimization
        
        # Define optimization hierarchy (higher index = more capable)
        hierarchy = [
            CPUOptimization.GENERIC,
            CPUOptimization.AVX2,
            CPUOptimization.AVX512,
        ]
        
        # AUTO and NATIVE are handled separately, skip validation
        if requested_level not in hierarchy:
            return
        if machine_level not in hierarchy:
            return
            
        machine_idx = hierarchy.index(machine_level)
        requested_idx = hierarchy.index(requested_level)
        
        if requested_idx < machine_idx:
            # User requested lower optimization than machine supports
            logger.error(
                f"❌ CPU mode mismatch (exit code {EXIT_CPU_MODE_MISMATCH}): "
                f"--cpu-{requested_level.value} requested, "
                f"but build machine supports {machine_level.value}.\n"
                f"\n"
                f"   Build machine: {self.cpu_capabilities.model_name}\n"
                f"   Detected: {self.cpu_capabilities.architecture} "
                f"({'AVX-512' if self.cpu_capabilities.has_avx512 else 'AVX2' if self.cpu_capabilities.has_avx2 else 'baseline'})\n"
                f"\n"
                f"   Since llama.cpp uses runtime SIMD detection, building with\n"
                f"   --cpu-{requested_level.value} on this machine gives suboptimal results:\n"
                f"   - Compiler uses -march={self._get_march_for_level(requested_level)} (suboptimal)\n"
                f"   - Runtime still uses {machine_level.value} paths (not compiler-optimized)\n"
                f"\n"
                f"   Solutions:\n"
                f"   1. Use --cpu-native (recommended for local builds)\n"
                f"   2. Use --cpu-{machine_level.value} to match this machine\n"
                f"   3. Build on a machine that matches your target deployment\n"
            )
            sys.exit(EXIT_CPU_MODE_MISMATCH)
    
    def _get_march_for_level(self, level: CPUOptimization) -> str:
        """Get -march value for a CPU optimization level."""
        march_map = {
            CPUOptimization.GENERIC: "x86-64",
            CPUOptimization.AVX2: "x86-64-v3",
            CPUOptimization.AVX512: "x86-64-v4",
            CPUOptimization.NATIVE: "native",
        }
        return march_map.get(level, "x86-64")

    def get_cpu_flags(self) -> dict[str, str]:
        """Get CPU-related compiler flags."""
        # Native mode: aggressive optimizations matching baremetal build script
        native_cflags = (
            "-O3 -march=native -mtune=native "
            "-ffast-math -fno-finite-math-only -funroll-loops -fomit-frame-pointer "
            "-falign-functions=32 -falign-loops=32 "
            "-fprefetch-loop-arrays -ftree-vectorize "
            "-fno-signed-zeros -fno-trapping-math"
        )

        # For NATIVE mode, detect CPU features dynamically
        if self.cpu_optimization == CPUOptimization.NATIVE:
            return {
                "arch": "native",
                "cflags": native_cflags,
                "avx512": "ON" if self.cpu_capabilities.has_avx512 else "OFF",
                "avx512_vbmi": "ON" if self.cpu_capabilities.has_avx512_vbmi else "OFF",
                "avx512_vnni": "ON" if self.cpu_capabilities.has_avx512_vnni else "OFF",
                "avx2": "ON" if self.cpu_capabilities.has_avx2 else "OFF",
                "fma": "ON" if self.cpu_capabilities.has_fma else "OFF",
                "f16c": "ON" if self.cpu_capabilities.has_f16c else "OFF",
            }

        flags = {
            CPUOptimization.AVX512: {
                "arch": "x86-64-v4",
                "cflags": "-O3 -march=x86-64-v4 -mtune=generic -ffast-math -fno-finite-math-only",
                "avx512": "ON",
                "avx512_vbmi": "ON",
                "avx512_vnni": "ON",
                "avx2": "ON",
                "fma": "ON",
                "f16c": "ON",
            },
            CPUOptimization.AVX2: {
                "arch": "x86-64-v3",
                "cflags": "-O3 -march=x86-64-v3 -mtune=generic -ffast-math -fno-finite-math-only",
                "avx512": "OFF",
                "avx512_vbmi": "OFF",
                "avx512_vnni": "OFF",
                "avx2": "ON",
                "fma": "ON",
                "f16c": "ON",
            },
            CPUOptimization.GENERIC: {
                "arch": "x86-64",
                "cflags": "-O3 -march=x86-64 -mtune=generic -fno-finite-math-only",
                "avx512": "OFF",
                "avx512_vbmi": "OFF",
                "avx512_vnni": "OFF",
                "avx2": "OFF",
                "fma": "OFF",
                "f16c": "OFF",
            },
        }
        return flags.get(self.cpu_optimization, flags[CPUOptimization.GENERIC])

    def get_cuda_architectures(self) -> str:
        """Get CUDA architectures string."""
        if self.gpu_arch == "multi":
            # Multi-architecture build for maximum portability
            # Ampere (80, 86, 87), Ada (89), Hopper (90), Blackwell (120)
            return "80;86;87;89;90;120"
        else:
            # Single architecture - format: "120" -> "120"
            return self.gpu_arch

    def get_cmake_args(self) -> list[str]:
        """Generate CMAKE_ARGS list for llama-cpp-python."""
        cpu_flags = self.get_cpu_flags()
        args = []

        # CUDA configuration
        if self.cuda_enabled:
            args.extend(
                [
                    "-DGGML_CUDA=ON",
                    "-DGGML_CUDA_F16=ON",
                    "-DGGML_CUDA_FORCE_MMQ=ON",
                    "-DGGML_CUDA_FORCE_CUBLAS=ON",
                    f"-DCMAKE_CUDA_ARCHITECTURES={self.get_cuda_architectures()}",
                ]
            )
        else:
            args.extend(
                [
                    "-DGGML_CUDA=OFF",
                    "-DGGML_METAL=OFF",
                ]
            )

        # BLAS configuration
        if self.blas_enabled:
            args.extend(
                [
                    "-DGGML_BLAS=ON",
                    "-DGGML_BLAS_VENDOR=OpenBLAS",
                ]
            )

        # CPU optimization flags (GGML_ prefix for llama.cpp cmake options)
        # Use GGML_NATIVE for native builds, explicit SIMD flags for portable builds
        if self.cpu_optimization == CPUOptimization.NATIVE:
            # Native: Let llama.cpp use runtime detection with -march=native
            args.extend(
                [
                    "-DGGML_NATIVE=ON",
                    f'-DCMAKE_CXX_FLAGS="{cpu_flags["cflags"]}"',
                    f'-DCMAKE_C_FLAGS="{cpu_flags["cflags"]}"',
                ]
            )
        else:
            # Portable: Disable GGML_NATIVE and set explicit SIMD flags
            args.extend(
                [
                    "-DGGML_NATIVE=OFF",
                    f"-DGGML_AVX512={cpu_flags['avx512']}",
                    f"-DGGML_AVX512_VBMI={cpu_flags['avx512_vbmi']}",
                    f"-DGGML_AVX512_VNNI={cpu_flags['avx512_vnni']}",
                    f"-DGGML_AVX2={cpu_flags['avx2']}",
                    f"-DGGML_FMA={cpu_flags['fma']}",
                    f"-DGGML_F16C={cpu_flags['f16c']}",
                    f'-DCMAKE_CXX_FLAGS="{cpu_flags["cflags"]}"',
                    f'-DCMAKE_C_FLAGS="{cpu_flags["cflags"]}"',
                ]
            )

        # Build options
        args.extend(
            [
                f"-DGGML_BUILD_TESTS={'ON' if self.build_tests else 'OFF'}",
                f"-DGGML_BUILD_EXAMPLES={'ON' if self.build_examples else 'OFF'}",
                f"-DLLAMA_BUILD_TESTS={'ON' if self.build_tests else 'OFF'}",
                f"-DLLAMA_BUILD_EXAMPLES={'ON' if self.build_examples else 'OFF'}",
            ]
        )

        return args

    def get_cmake_args_string(self) -> str:
        """Get CMAKE_ARGS as a single string (for shell/Docker)."""
        return " ".join(self.get_cmake_args())

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables for build."""
        return {
            "CMAKE_ARGS": self.get_cmake_args_string(),
            "CMAKE_BUILD_PARALLEL_LEVEL": str(os.cpu_count() // 2 or 1),
            "FORCE_CMAKE": "1",
        }

    def summary(self) -> str:
        """Get human-readable configuration summary."""
        cpu_flags = self.get_cpu_flags()

        # Show if native/auto mode was used
        if self.cpu_mode == "native":
            cpu_mode_str = "native (-march=native, max performance)"
        elif self.cpu_mode == "auto":
            cpu_mode_str = f"auto (detected -> {self.cpu_optimization.value})"
        else:
            cpu_mode_str = self.cpu_optimization.value

        # Show if single GPU arch vs multi
        if self.gpu_arch != "multi":
            gpu_arch_str = f"{self.gpu_arch} (native/single-arch)"
        else:
            gpu_arch_str = f"{self.get_cuda_architectures()} (multi-arch portable)"

        lines = [
            "🔧 llama-cpp-python Build Configuration",
            "=" * 50,
            f"CPU: {self.cpu_capabilities.model_name}",
            f"  Mode: {cpu_mode_str}",
            f"  Target: {cpu_flags['arch']}",
            f"  AVX-512: {cpu_flags['avx512']}",
            f"  AVX2: {cpu_flags['avx2']}",
            f"  FMA: {cpu_flags['fma']}",
        ]

        if self.cuda_enabled:
            lines.extend(
                [
                    "GPU: CUDA enabled",
                    f"  Target: {gpu_arch_str}",
                ]
            )
        else:
            lines.append("GPU: Disabled (CPU-only build)")

        return "\n".join(lines)


def main():
    """CLI entry point for Docker builds."""
    parser = argparse.ArgumentParser(
        description="Generate CMAKE configuration for llama-cpp-python builds",
        epilog="""
CPU Optimization Levels:
  avx2 (default)  - Portable, works on Intel 2013+/AMD 2015+, 2-3x faster
  avx512          - Modern servers only (Intel 2019+/AMD 2022+), 4-6x faster
  generic         - Maximum portability, slowest
  auto            - Detect from current CPU (for local/native builds only)

Examples:
  # Docker build (portable AVX2 - default):
  python cmake_config.py --output-env

  # Local native build (auto-detect):
  python cmake_config.py --cpu-mode=auto --output-env

  # Modern server deployment:
  python cmake_config.py --cpu-mode=avx512 --output-env
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cpu-mode",
        choices=["native", "auto", "avx512", "avx2", "generic"],
        default="avx2",
        help="CPU optimization mode: native (max perf), auto (detect level), avx512/avx2/generic (portable)",
    )
    parser.add_argument(
        "--gpu-arch",
        default="multi",
        help="GPU architecture: 'multi' for portable, or specific like '120' (default: multi)",
    )
    parser.add_argument(
        "--no-cuda",
        action="store_true",
        help="Disable CUDA (CPU-only build)",
    )
    parser.add_argument(
        "--output-env",
        action="store_true",
        help="Output as shell environment variables (for eval)",
    )
    parser.add_argument(
        "--output-json",
        action="store_true",
        help="Output as JSON",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print human-readable summary",
    )

    args = parser.parse_args()

    # Configure logging for CLI (scripts run in Docker without logging setup)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    generator = CMakeConfigGenerator(
        cpu_mode=args.cpu_mode,
        gpu_arch=args.gpu_arch,
        cuda_enabled=not args.no_cuda,
    )

    if args.summary:
        print(generator.summary(), file=sys.stderr)
        print("", file=sys.stderr)

    if args.output_json:
        output = {
            "cmake_args": generator.get_cmake_args(),
            "cmake_args_string": generator.get_cmake_args_string(),
            "env_vars": generator.get_env_vars(),
            "cpu_optimization": generator.cpu_optimization.value,
            "cpu_architecture": generator.get_cpu_flags()["arch"],
            "cuda_architectures": generator.get_cuda_architectures(),
        }
        print(json.dumps(output, indent=2))
    elif args.output_env:
        for key, value in generator.get_env_vars().items():
            # Shell-safe export format - escape inner double quotes
            escaped_value = value.replace('"', '\\"')
            print(f'export {key}="{escaped_value}"')
    else:
        # Default: print CMAKE_ARGS
        print(generator.get_cmake_args_string())


if __name__ == "__main__":
    main()
