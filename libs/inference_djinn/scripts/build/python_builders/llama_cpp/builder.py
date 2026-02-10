"""llama-cpp-python wheel builder."""

import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# Ensure python_builders is in path for imports
_script_dir = Path(__file__).parent.resolve()
_python_builders_dir = _script_dir.parent
if str(_python_builders_dir) not in sys.path:
    sys.path.insert(0, str(_python_builders_dir))

# Add llama_cpp builder dir to path for patch_workflows package import
# (Required because builder.py is loaded dynamically via spec_from_file_location,
# which prevents relative imports from working)
if str(_script_dir) not in sys.path:
    sys.path.insert(0, str(_script_dir))

from common.cmake_config import CMakeConfigGenerator
from common.config import BuildConfig, CPUMode
from common.environment import EnvironmentManager
from common.utils import git_clone_with_retry

logger = logging.getLogger(__name__)


class LlamaCppBuilder:
    """Build llama-cpp-python wheel with GPU/CPU optimizations."""

    # NOTE: llama-cpp-python does NOT require PyTorch.
    # It uses its own CUDA/HIP backend for GPU acceleration.

    def __init__(
        self,
        config: BuildConfig,
        llama_cpp_python_version: str | None = None,
        keep_sources: bool = False,
        target_dir: str | None = None,
        no_deps: bool = False,
        force_patches: bool = False,
    ):
        self.config = config
        self.llama_cpp_python_version = (
            llama_cpp_python_version  # Pin to specific version (empty = latest)
        )
        self.keep_sources = keep_sources  # Preserve source dir after build
        self.target_dir = target_dir  # Install to specific directory (for Docker)
        self.no_deps = no_deps  # Install without dependencies
        self.force_patches = force_patches
        self.env_manager = EnvironmentManager()

        # Paths - use /tmp for builds to avoid NFS overhead
        self.repo_root = Path(config.repo_root or self._find_repo_root())
        self.extra_sources_dir = Path("/tmp") / "llama-cpp-python-build"
        self.source_dir = self.extra_sources_dir / "llama-cpp-python"
        self._wheel_dir: Path | None = None  # Set during build

        # Update config paths
        self.config.repo_root = str(self.repo_root)
        self.config.venv_dir = str(self.env_manager.venv_dir)
        self.config.source_dir = str(self.source_dir)

    def _find_repo_root(self) -> Path:
        """Find repository root."""
        script_dir = Path(__file__).parent.resolve()
        # Assuming structure: repo_root/libs/inference_djinn/scripts/build/python_builders/llama_cpp/
        return script_dir.parent.parent.parent.parent.parent.parent

    # Build dependencies required for llama-cpp-python with --no-build-isolation
    BUILD_DEPENDENCIES = [
        "scikit-build-core[pyproject]>=0.9.2",
        "cmake>=3.21",
        "ninja",
    ]

    def _ensure_build_dependencies(self):
        """
        Ensure build dependencies are installed.

        Since we use --no-build-isolation for better control over the build
        environment and to preserve PyTorch nightly, we must pre-install the
        build dependencies that would normally be installed automatically.
        """
        logger.info("   Checking build dependencies...")

        missing_deps = []

        # Check scikit-build-core
        try:
            import scikit_build_core

            logger.info(
                f"   ✅ scikit-build-core {scikit_build_core.__version__} installed"
            )
        except ImportError:
            missing_deps.append("scikit-build-core[pyproject]>=0.9.2")

        # Check cmake (Python package provides cmake command)
        try:
            result = subprocess.run(
                ["cmake", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                cmake_version = result.stdout.split("\n")[0]
                logger.info(f"   ✅ {cmake_version}")
            else:
                missing_deps.append("cmake>=3.21")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            missing_deps.append("cmake>=3.21")

        # Check ninja
        try:
            result = subprocess.run(
                ["ninja", "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                ninja_version = result.stdout.strip()
                logger.info(f"   ✅ ninja {ninja_version}")
            else:
                missing_deps.append("ninja")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            missing_deps.append("ninja")

        # Install missing dependencies
        if missing_deps:
            logger.info(
                f"   📦 Installing build dependencies: {', '.join(missing_deps)}"
            )
            subprocess.run(
                ["pip", "install", "--quiet"] + missing_deps,
                check=True,
            )
            logger.info("   ✅ Build dependencies installed")

    def _cleanup_wheel_dir(self):
        """Clean up temporary wheel directory to prevent disk space accumulation."""
        import shutil

        if self._wheel_dir is not None and self._wheel_dir.exists():
            try:
                shutil.rmtree(self._wheel_dir)
                logger.info(f"🧹 Cleaned up temp directory: {self._wheel_dir}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to clean up {self._wheel_dir}: {e}")

    def _cleanup_source_dir(self):
        """Clean up source directory after successful install (unless keep_sources is set)."""
        import shutil

        if self.keep_sources:
            logger.info(f"📁 Source directory preserved: {self.extra_sources_dir}")
            return

        if self.extra_sources_dir.exists():
            try:
                shutil.rmtree(self.extra_sources_dir)
                logger.info(f"🧹 Cleaned up source directory: {self.extra_sources_dir}")
            except Exception as e:
                logger.warning(f"⚠️  Failed to clean up {self.extra_sources_dir}: {e}")

    def build(self):
        """Execute complete build process with cleanup on failure."""
        logger.info("=" * 80)
        logger.info("🚀 llama-cpp-python Wheel Builder")
        logger.info("=" * 80)
        logger.info("")

        # Display configuration
        logger.info(self.config.summary())
        logger.info("")

        try:
            # Execute build steps
            self._ensure_prerequisites()
            self._ensure_source_tree()
            self._apply_patches()
            self._configure_build_environment()
            self._build_wheel()
            self._install_wheel()
            self._verify_installation()

            logger.info("")
            logger.info("=" * 80)
            logger.info("✅ llama-cpp-python build complete!")
            logger.info("=" * 80)

            # Clean up source directory after successful install
            self._cleanup_source_dir()

        except KeyboardInterrupt:
            logger.error("\n❌ Build interrupted by user (Ctrl+C)")
            logger.info("   Source preserved for debugging: {self.extra_sources_dir}")
            raise

        except Exception as e:
            logger.error(f"\n❌ Build failed: {e}")
            logger.info(f"   Source preserved for debugging: {self.extra_sources_dir}")
            raise

        finally:
            # Clean up temp wheel directory to prevent disk space accumulation
            self._cleanup_wheel_dir()

    def _ensure_prerequisites(self):
        """Check prerequisites."""
        logger.info("🔍 Checking prerequisites...")

        # Ensure build dependencies are installed (required for --no-build-isolation)
        self._ensure_build_dependencies()

        # NOTE: PyTorch is NOT required for llama-cpp-python builds.
        # llama-cpp-python uses its own CUDA/HIP backend, not PyTorch.
        # PyTorch nightly is only needed for vLLM builds.
        # The vLLM builder will auto-install PyTorch nightly if needed.

        # Check for nvcc
        try:
            result = subprocess.run(
                ["nvcc", "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            nvcc_version = result.stdout.split("release")[-1].split(",")[0].strip()
            logger.info(f"   ✅ NVCC (CUDA compiler) {nvcc_version}")
        except (
            subprocess.CalledProcessError,
            FileNotFoundError,
            subprocess.TimeoutExpired,
        ):
            logger.warning("   ⚠️  NVCC not found - GPU builds may fail")

    def _ensure_source_tree(self):
        """Clone or update llama-cpp-python source."""
        logger.info("📥 Ensuring llama-cpp-python source tree...")

        self.extra_sources_dir.mkdir(parents=True, exist_ok=True)

        if (self.source_dir / ".git").exists():
            logger.info(f"   ✅ Source tree present: {self.source_dir}")
            # Fetch latest
            logger.info("   🔄 Fetching latest llama-cpp-python...")
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=self.source_dir,
                check=False,
            )
        else:
            # Clone llama-cpp-python with retry logic
            logger.info(f"   📦 Cloning llama-cpp-python to {self.source_dir}...")
            repo_url = os.environ.get(
                "LLAMA_REPO_URL", "https://github.com/abetlen/llama-cpp-python.git"
            )
            git_clone_with_retry(repo_url, self.source_dir)

        # Checkout specific version if requested, otherwise use latest
        if self.llama_cpp_python_version:
            logger.info(
                f"   🔄 Checking out version {self.llama_cpp_python_version}..."
            )
            result = subprocess.run(
                ["git", "checkout", self.llama_cpp_python_version],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error(
                    f"   ❌ Failed to checkout {self.llama_cpp_python_version}"
                )
                logger.error(f"   {result.stderr.strip()}")
                raise RuntimeError(
                    f"Failed to checkout llama-cpp-python version: {self.llama_cpp_python_version}"
                )
        else:
            # Use latest from main
            logger.info("   🔄 Using latest from main branch...")
            subprocess.run(
                ["git", "checkout", "origin/main"],
                cwd=self.source_dir,
                check=False,
            )

        # Get current commit info
        commit = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=self.source_dir,
            capture_output=True,
            text=True,
        )
        logger.info(f"   ✅ llama-cpp-python at: {commit.stdout.strip()}")

        # Update llama.cpp submodule (let it use its pinned version)
        llama_cpp_dir = self.source_dir / "vendor" / "llama.cpp"
        if (self.source_dir / ".gitmodules").exists():
            logger.info("   🔄 Updating llama.cpp submodule...")
            # Reset submodule to clean state first (removes old forced checkouts)
            if llama_cpp_dir.exists():
                subprocess.run(
                    ["git", "reset", "--hard"],
                    cwd=llama_cpp_dir,
                    check=False,
                    capture_output=True,
                )
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive", "--force"],
                cwd=self.source_dir,
                check=False,
            )

            # Show llama.cpp version
            if llama_cpp_dir.exists():
                llama_commit = subprocess.run(
                    ["git", "log", "-1", "--oneline"],
                    cwd=llama_cpp_dir,
                    capture_output=True,
                    text=True,
                )
                logger.info(
                    f"   ✅ llama.cpp submodule at: {llama_commit.stdout.strip()}"
                )

        logger.info("   ✅ Clone complete")

    def _apply_patches(self):
        """Apply version-specific patches to llama-cpp-python source."""
        from patch_workflows import LlamaCppPatcher

        # Get current version from git
        version_result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=self.source_dir,
            capture_output=True,
            text=True,
        )
        version = (
            version_result.stdout.strip()
            if version_result.returncode == 0
            else "unknown"
        )

        patcher = LlamaCppPatcher(
            source_dir=self.source_dir,
            version=version,
            force_patches=self.force_patches,
        )
        patcher.apply_patches()

    def _get_cmake_args(self) -> list[str]:
        """
        Get CMake arguments for llama-cpp-python build.

        Uses unified CMakeConfigGenerator for CPU/GPU flags, then adds
        llama.cpp-specific optimizations.
        """
        # Map BuildConfig cpu_mode to cmake_config cpu_mode string
        cpu_mode_map = {
            CPUMode.NATIVE: "native",  # True native: -march=native -mtune=native + aggressive opts
            CPUMode.AVX512: "avx512",  # AVX-512: -march=x86-64-v4 (Intel server, AMD Zen4+)
            CPUMode.AVX2: "avx2",
            CPUMode.GENERIC: "generic",
        }
        cpu_mode_str = cpu_mode_map.get(self.config.cpu_mode, "avx2")

        # Use unified CMakeConfigGenerator for core CPU/GPU flags
        # NOTE: BLAS disabled - OpenBLAS threading conflicts with llama.cpp's OpenMP,
        # causing severe performance degradation in hybrid GPU+CPU inference
        cmake_generator = CMakeConfigGenerator(
            cpu_mode=cpu_mode_str,
            gpu_arch=self.config.gpu_arch or "multi",
            cuda_enabled=True,
            blas_enabled=False,  # Disabled: conflicts with OpenMP threading
            build_tests=False,
            build_examples=False,
            force=self.config.force,
        )

        # Start with base args from unified generator
        args = cmake_generator.get_cmake_args()

        # Add llama.cpp-specific optimizations (not in base CMakeConfigGenerator)
        # See BUILD_CONFIG.md for flag behavior details.
        args.extend(
            [
                "-DCMAKE_BUILD_TYPE=Release",
                "-DBUILD_NUMBER=0",  # Fixes LLAMA_INSTALL_VERSION issue
                "-DGGML_FP16_VA=ON",  # FP16 vector accumulator
                "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-O3,-s",
                "-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-O3,-s",
            ]
        )

        # Optional CUDA matrix-vector tuning (silently ignored if unsupported)
        # These may have marginal effect; remove if benchmarks show no benefit
        if self.config.gpu_arch is not None:
            args.extend(
                [
                    "-DGGML_CUDA_DMMV_X=256",
                    "-DGGML_CUDA_MMV_Y=8",
                    "-DGGML_CUDA_DMMV_Y=4",
                ]
            )

        # Add CUDA compiler flags for optimized single-arch builds
        # Note: CUDA flags must be quoted to prevent shell splitting on spaces
        cuda_flags = self._get_cuda_flags()
        if cuda_flags:
            # Use semicolons as CMake list separator to avoid shell word splitting
            # CMAKE_ARGS is space-separated, so embedded spaces in flag values cause issues
            args.append(f'-DCMAKE_CUDA_FLAGS="{cuda_flags}"')

        return args

    def _get_cuda_flags(self) -> str:
        """
        Get CUDA compiler flags for optimized single-architecture builds.

        Returns empty string for multi-arch builds (base generator handles those).
        """
        # For single architecture builds, add extra NVCC optimization flags
        if self.config.gpu_arch is not None and self.config.gpu_arch != "multi":
            # Parse architecture using validated helper (e.g., "120" -> "12", "0")
            arch = self.config.gpu_arch
            arch_major, arch_minor = self.config._parse_gpu_arch()

            return (
                "--use_fast_math -O3 --optimize=3 "
                "--maxrregcount=0 "
                "--ptxas-options=-v,-O3,-allow-expensive-optimizations=true "
                f"--compiler-options=-O3,{self.config.cpu_flags.replace(' ', ',')} "
                f"-gencode=arch=compute_{arch_major}{arch_minor},code=sm_{arch} "
                f"--gpu-architecture=sm_{arch} "
                "--threads=0"
            )
        else:
            # Multi-architecture build - let base CMAKE_CUDA_ARCHITECTURES handle it
            # Only add base optimization flags
            return (
                "--use_fast_math -O3 --optimize=3 "
                "--maxrregcount=0 "
                "--ptxas-options=-v,-O3,-allow-expensive-optimizations=true "
                "--threads=0"
            )

    def _configure_build_environment(self):
        """Configure CMake build environment."""
        logger.info("⚙️  Configuring CMake build environment...")

        # Get CMake args
        cmake_args = self._get_cmake_args()

        # Set CMAKE_ARGS environment variable
        os.environ["CMAKE_ARGS"] = " ".join(cmake_args)
        os.environ["CMAKE_BUILD_PARALLEL_LEVEL"] = str(self.config.max_jobs)

        logger.info(f"   CMake arguments: {len(cmake_args)} flags configured")
        logger.info(f"   Parallel jobs: {self.config.max_jobs}")

        # CUDA environment
        cuda_home = os.environ.get("CUDA_HOME", "/usr/local/cuda")
        os.environ["CUDACXX"] = f"{cuda_home}/bin/nvcc"
        os.environ["CUDA_HOME"] = cuda_home

        # Add CUDA to PATH if not already
        path = os.environ.get("PATH", "")
        cuda_bin = f"{cuda_home}/bin"
        if cuda_bin not in path:
            os.environ["PATH"] = f"{cuda_bin}:{path}"

        # CUDA runtime optimizations
        os.environ["CUDA_LAUNCH_BLOCKING"] = "0"
        os.environ["CUDA_CACHE_DISABLE"] = "0"
        os.environ["CUDA_DEVICE_MAX_CONNECTIONS"] = "32"
        os.environ["CUDA_MODULE_LOADING"] = "LAZY"

        logger.info("   ✅ Environment configured")

        # Log summary
        if self.config.verbose:
            logger.debug("   CMAKE_ARGS:")
            for arg in cmake_args:
                logger.debug(f"     {arg}")

    def _build_wheel(self):
        """Build the wheel to a temp directory."""
        logger.info("")
        logger.info("🔨 Building llama-cpp-python wheel...")
        logger.info(
            f"   This may take 10-30 minutes depending on parallelism ({self.config.max_jobs} jobs)"
        )
        logger.info("")

        # Create temp directory for wheel output
        self._wheel_dir = Path(tempfile.mkdtemp(prefix="llama_cpp_wheel_"))
        logger.info(f"   Wheel output: {self._wheel_dir}")

        # Build wheel
        subprocess.run(
            [
                "pip",
                "wheel",
                "--no-deps",
                "--no-build-isolation",
                "--no-cache-dir",
                "--wheel-dir",
                str(self._wheel_dir),
                str(self.source_dir),
            ],
            check=True,
            cwd=self.source_dir,
        )

        logger.info("")
        logger.info("   ✅ Wheel built successfully")

    def _install_wheel(self):
        """Install the built wheel."""
        logger.info("📦 Installing llama-cpp-python wheel...")

        if self._wheel_dir is None:
            raise RuntimeError("Wheel directory not set - build step may have failed")

        # Find the wheel file
        wheel_files = list(self._wheel_dir.glob("llama_cpp_python-*.whl"))
        if not wheel_files:
            raise RuntimeError(f"No llama-cpp-python wheel found in {self._wheel_dir}")

        # Get the most recent wheel
        wheel_file = max(wheel_files, key=lambda p: p.stat().st_mtime)
        logger.info(f"   Wheel: {wheel_file.name}")

        # Build install command
        install_cmd = ["pip", "install", "--force-reinstall"]

        # Add --no-deps if requested (default for Docker to preserve PyTorch)
        if self.no_deps:
            install_cmd.append("--no-deps")
            logger.info("   Using --no-deps (preserving existing packages)")

        # Add --target if specified (for Docker builds)
        if self.target_dir:
            install_cmd.extend(["--target", self.target_dir])
            logger.info(f"   Installing to target: {self.target_dir}")

        install_cmd.append(str(wheel_file))

        subprocess.run(install_cmd, check=True)

        logger.info("   ✅ llama-cpp-python installed")

    def _verify_installation(self):
        """Verify installation after build."""
        logger.info("🔍 Verifying installation...")

        # NOTE: PyTorch verification removed - llama-cpp-python does not require PyTorch.
        # It uses its own CUDA/HIP backend.

        # Verify llama-cpp-python was installed
        try:
            import llama_cpp

            # Get version from package metadata (llama_cpp may not have __version__)
            try:
                from importlib.metadata import version

                llama_version = version("llama-cpp-python")
            except Exception:
                llama_version = getattr(llama_cpp, "__version__", "unknown")

            logger.info(f"   ✅ llama_cpp installed: {llama_version}")

            # Check GPU support
            if hasattr(llama_cpp, "llama_supports_gpu_offload"):
                gpu_offload = llama_cpp.llama_supports_gpu_offload()
                if gpu_offload:
                    logger.info("   ✅ GPU offload supported")
                else:
                    logger.warning("   ⚠️  GPU offload not supported")
        except ImportError:
            raise RuntimeError(
                "❌ llama_cpp import failed after installation!\n"
                "   Check wheel installation logs for errors."
            )
