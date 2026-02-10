#!/usr/bin/env python3
"""
llama-cpp-python Wheel Builder - Main Entry Point

Build GPU-optimized llama-cpp-python wheel with flexible CPU/GPU architecture targeting
and smart build parallelization.
"""

import argparse
import logging
import sys
from pathlib import Path

# Add python_builders to path for imports
script_dir = Path(__file__).parent.resolve()
python_builders_dir = script_dir.parent
if str(python_builders_dir) not in sys.path:
    sys.path.insert(0, str(python_builders_dir))

# Import from common module (direct path, not package)
# Import builder from current directory (avoid conflict with actual llama_cpp package)
import importlib.util as _importlib_util

from common.config import BuildConfig, CPUMode, GPUMode, JobsMode

_builder_spec = _importlib_util.spec_from_file_location(
    "llama_cpp_builder", script_dir / "builder.py"
)
if _builder_spec is None or _builder_spec.loader is None:
    raise ImportError(f"Failed to load builder.py from {script_dir / 'builder.py'}")
_builder_module = _importlib_util.module_from_spec(_builder_spec)
_builder_spec.loader.exec_module(_builder_module)
LlamaCppBuilder = _builder_module.LlamaCppBuilder




def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Build GPU-optimized llama-cpp-python wheel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: native build optimized for current hardware (CPU + GPU auto-detected)
  %(prog)s

  # Explicit native build (same as default, for clarity)
  %(prog)s --cpu-native --gpu-native

  # Maximum build speed (use all CPU threads)
  %(prog)s --jobs-max

  # Portable AVX2 build for Docker/deployment (RECOMMENDED for containers)
  %(prog)s --cpu-avx2

  # Portable AVX2 + multi-GPU architecture (maximum portability with good performance)
  %(prog)s --cpu-avx2 --gpu-generic

  # Maximum portability (slowest, baseline x86-64)
  %(prog)s --generic

  # Custom configuration
  %(prog)s --cpu-avx2 --gpu-arch=89 --jobs=12

Docker Usage:
  # Build with portable optimizations, install to target directory
  %(prog)s --cpu-avx2 --gpu-generic --target=/build/packages --no-deps

  # Native build inside Docker (for single-machine deployment)
  %(prog)s --cpu-native --gpu-native --target=/build/packages --no-deps
        """,
    )

    # CPU optimization
    cpu_group = parser.add_mutually_exclusive_group()
    cpu_group.add_argument(
        "--cpu-native",
        action="store_true",
        help="Optimize for current CPU (default, -march=native)",
    )
    cpu_group.add_argument(
        "--cpu-avx512",
        action="store_true",
        help="AVX-512 build (-march=x86-64-v4, 4-6x faster, Intel server 2019+/AMD Zen4+)",
    )
    cpu_group.add_argument(
        "--cpu-avx2",
        action="store_true",
        help="Portable AVX2 build (-march=x86-64-v3, 2-3x faster, Intel 2013+/AMD 2015+)",
    )
    cpu_group.add_argument(
        "--cpu-generic",
        action="store_true",
        help="Maximum portability (-march=x86-64, baseline performance)",
    )

    # GPU optimization
    gpu_group = parser.add_mutually_exclusive_group()
    gpu_group.add_argument(
        "--gpu-native",
        action="store_true",
        help="Optimize for current GPU (default, auto-detect single architecture)",
    )
    gpu_group.add_argument(
        "--gpu-generic",
        action="store_true",
        help="Generic GPU optimization (multi-arch, portable)",
    )
    gpu_group.add_argument(
        "--gpu-arch",
        type=str,
        metavar="CODE",
        help="Optimize for specific GPU (e.g., 89 for RTX 4090, 120 for RTX 5090)",
    )

    # Build parallelization
    jobs_group = parser.add_mutually_exclusive_group()
    jobs_group.add_argument(
        "--jobs-conservative",
        action="store_true",
        help="Use half of available threads (default, stable)",
    )
    jobs_group.add_argument(
        "--jobs-max",
        action="store_true",
        help="Use all threads - 1 (maximum speed, may be sluggish)",
    )
    jobs_group.add_argument(
        "--jobs", type=int, metavar="N", help="Use N threads (custom)"
    )

    # Convenience flags
    parser.add_argument(
        "--portable",
        action="store_true",
        help="Portable optimized build for Docker (equivalent to --cpu-avx2 --gpu-generic)",
    )
    parser.add_argument(
        "--generic",
        action="store_true",
        help="Maximum portability build (equivalent to --cpu-generic --gpu-generic)",
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose output (debug logging)"
    )

    parser.add_argument(
        "--keep-sources",
        action="store_true",
        help="Keep source directory after build for inspection (default: clean up)",
    )

    # Source options
    parser.add_argument(
        "--llama-cpp-python-version",
        type=str,
        metavar="VERSION",
        help="Pin llama-cpp-python version (commit hash or tag, e.g., ce6fd8b or v0.3.8). Default: latest from main",
    )


    # Docker/install options
    parser.add_argument(
        "--target",
        type=str,
        metavar="DIR",
        help="Install to target directory (for Docker builds, e.g., --target=/build/packages)",
    )
    parser.add_argument(
        "--no-deps",
        action="store_true",
        help="Install without dependencies (preserves existing packages like PyTorch)",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="(Deprecated: no longer required for explicit CPU modes) Legacy compatibility flag",
    )

    parser.add_argument(
        "--force-patches",
        action="store_true",
        help="Apply experimental patches even for unverified versions",
    )

    return parser.parse_args()


def args_to_config(args) -> BuildConfig:
    """Convert parsed arguments to BuildConfig."""

    # Determine CPU mode
    # --portable implies --cpu-avx2
    if args.portable or args.cpu_avx2:
        cpu_mode = CPUMode.AVX2  # Portable optimized (recommended for Docker)
    elif args.cpu_avx512:
        cpu_mode = CPUMode.AVX512  # AVX-512 (Intel server, AMD Zen4+)
    elif args.generic or args.cpu_generic:
        cpu_mode = CPUMode.GENERIC  # Maximum portability (baseline x86-64)
    else:
        cpu_mode = CPUMode.NATIVE  # Default: optimal for local machine

    # Determine GPU mode and architecture
    # --portable implies --gpu-generic
    if args.portable or args.generic or args.gpu_generic:
        gpu_mode = GPUMode.GENERIC
        gpu_arch = None  # Will be set to "multi" in BuildConfig
    elif args.gpu_arch:
        gpu_mode = GPUMode.CUSTOM
        gpu_arch = args.gpu_arch
    else:
        gpu_mode = GPUMode.OPTIMIZED
        gpu_arch = None  # Will be auto-detected in BuildConfig

    # Determine jobs mode
    if args.jobs is not None:
        jobs_mode = JobsMode.CUSTOM
        max_jobs = args.jobs
    elif args.jobs_max:
        jobs_mode = JobsMode.MAXIMUM
        max_jobs = None  # Will be computed in BuildConfig
    else:
        jobs_mode = JobsMode.CONSERVATIVE
        max_jobs = None  # Will be computed in BuildConfig

    return BuildConfig(
        cpu_mode=cpu_mode,
        gpu_mode=gpu_mode,
        gpu_arch=gpu_arch,
        jobs_mode=jobs_mode,
        max_jobs=max_jobs,
        verbose=args.verbose,
        force=args.force,
    )


def main():
    """Main entry point."""
    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    try:
        # Build configuration
        config = args_to_config(args)

        # Determine llama.cpp source mode (default: pinned for stability)
        # Get llama-cpp-python version (None = use latest from main)
        llama_cpp_python_version = getattr(args, 'llama_cpp_python_version', None)

        builder = LlamaCppBuilder(
            config,
            llama_cpp_python_version=llama_cpp_python_version,
            keep_sources=args.keep_sources,
            target_dir=args.target,
            no_deps=args.no_deps,
            force_patches=args.force_patches,
        )
        builder.build()

        return 0

    except KeyboardInterrupt:
        logging.error("\n❌ Build interrupted by user")
        return 130

    except Exception as e:
        logging.error(f"\n❌ Build failed: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
