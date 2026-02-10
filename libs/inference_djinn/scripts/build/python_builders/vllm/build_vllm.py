#!/usr/bin/env python3
"""
vLLM Wheel Builder - Main Entry Point

Build GPU-optimized vLLM wheel with flexible CPU/GPU architecture targeting
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
import importlib.util as _importlib_util  # noqa: E402

# Import builder from current directory (avoid conflict with actual vllm package)
from common.config import BuildConfig, CPUMode, GPUMode, JobsMode  # noqa: E402

_builder_spec = _importlib_util.spec_from_file_location(
    "vllm_builder", script_dir / "builder.py"
)
_builder_module = _importlib_util.module_from_spec(_builder_spec)
_builder_spec.loader.exec_module(_builder_module)
VLLMBuilder = _builder_module.VLLMBuilder


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Build GPU-optimized vLLM wheel",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Default: latest release tag optimized for current hardware
  # Patches applied automatically for verified versions (v0.13.x)
  %(prog)s

  # Bleeding edge (latest main branch) - patches NOT applied by default
  %(prog)s --vllm-version=main

  # Force patches for unverified versions (experimental)
  %(prog)s --vllm-version=v0.14.0 --apply-patches

  # Skip all patches (use vLLM default multi-arch build)
  %(prog)s --no-patches

  # Pin to specific release
  %(prog)s --vllm-version=v0.12.0

  # Pin to specific commit
  %(prog)s --vllm-version=abc123def

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
  # Build inside Docker container with portable AVX2 optimizations
  docker exec <container> python3 build_vllm.py --cpu-avx2 --gpu-generic

Patch Control:
  Architecture patches force single-GPU builds for optimal performance.
  By default, patches only apply for verified versions (v0.13.x).

  --apply-patches  Force patches for unverified versions (may not work)
  --no-patches     Skip patches entirely (multi-arch build)
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
        "--gpu-optimize",
        action="store_true",
        help="Optimize for current GPU (default, auto-detect)",
    )
    gpu_group.add_argument(
        "--gpu-generic",
        action="store_true",
        help="Generic GPU optimization (multi-arch)",
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

    # Docker/target installation
    parser.add_argument(
        "--target",
        type=str,
        metavar="DIR",
        help="Install to DIR instead of site-packages (for Docker builds)",
    )
    parser.add_argument(
        "--wheel-only",
        action="store_true",
        help="Build wheel only, don't install (output path printed to stdout)",
    )

    # Version control
    parser.add_argument(
        "--vllm-version",
        type=str,
        metavar="VERSION",
        help=(
            "vLLM version to build (default: latest release tag). "
            "Use 'main' for bleeding edge, or specify tag/commit hash."
        ),
    )

    # Patch control
    patch_group = parser.add_mutually_exclusive_group()
    patch_group.add_argument(
        "--apply-patches",
        action="store_true",
        help=(
            "Force apply architecture patches even for unverified vLLM versions. "
            "By default, patches only apply for v0.13.x (verified working)."
        ),
    )
    patch_group.add_argument(
        "--no-patches",
        action="store_true",
        help="Skip all architecture patches (use vLLM default build).",
    )

    return parser.parse_args()


def args_to_config(args) -> BuildConfig:
    """Convert parsed arguments to BuildConfig."""

    # Determine CPU mode
    # --portable implies --cpu-avx2
    if args.portable or args.cpu_avx2:
        cpu_mode = CPUMode.AVX2  # Portable optimized (recommended for Docker)
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
    )


def main():
    """Main entry point."""
    # Configure logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    args = parse_args()

    try:
        # Build configuration
        config = args_to_config(args)

        # Determine patch mode: force, skip, or default (version-gated)
        # --no-patches: skip all patches (use GPU generic mode)
        # --apply-patches: force patches even for unverified versions
        # default: apply patches only for verified versions (v0.13.x)
        if args.no_patches:
            # Force generic GPU mode to skip patching entirely
            from common.config import GPUMode

            config.gpu_mode = GPUMode.GENERIC
            config.gpu_arch = "multi"

        # Create builder with target directory and wheel-only options
        builder = VLLMBuilder(
            config,
            vllm_version=args.vllm_version,
            target_dir=args.target,
            wheel_only=args.wheel_only,
            keep_sources=args.keep_sources,
            force_patches=args.apply_patches,
        )
        wheel_path = builder.build()

        # If wheel-only, print the wheel path for downstream use
        if args.wheel_only and wheel_path:
            print(wheel_path)

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
