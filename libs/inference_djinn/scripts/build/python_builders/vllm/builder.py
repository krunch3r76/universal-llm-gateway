"""vLLM wheel builder."""

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

from common.config import BuildConfig, GPUMode
from common.environment import EnvironmentManager
from common.utils import git_clone_with_retry

logger = logging.getLogger(__name__)


class VLLMBuilder:
    """Build vLLM wheel with GPU/CPU optimizations."""

    # Required PyTorch nightly version (use version ranges)
    REQUIRED_PYTORCH_MIN_VERSION = "2.10.0.dev20251030"
    REQUIRED_CUDA_VERSION = "13.0"

    def __init__(
        self,
        config: BuildConfig,
        vllm_version: str | None = None,
        target_dir: str | None = None,
        wheel_only: bool = False,
        keep_sources: bool = False,
        force_patches: bool = False,
    ):
        self.config = config
        self.vllm_version = vllm_version
        self.env_manager = EnvironmentManager()
        self.target_dir: Path | None = Path(target_dir) if target_dir else None
        self.wheel_only: bool = wheel_only
        self.keep_sources: bool = keep_sources  # Preserve source dir after build
        self.force_patches: bool = (
            force_patches  # Apply patches even for unverified versions
        )

        # Paths - use /tmp for builds to avoid NFS overhead
        self.repo_root = Path(config.repo_root or self._find_repo_root())
        self.extra_sources_dir = Path("/tmp") / "vllm-build"
        self.source_dir = self.extra_sources_dir / "vllm"
        self._wheel_dir: Path | None = None  # Set during build
        self._wheel_file: Path | None = None  # Set during build
        self._detected_version: str | None = None  # Set after source checkout

        # Update config paths
        self.config.repo_root = str(self.repo_root)
        self.config.venv_dir = str(self.env_manager.venv_dir)
        self.config.source_dir = str(self.source_dir)

    def _find_repo_root(self) -> Path:
        """Find repository root."""
        script_dir = Path(__file__).parent.resolve()
        # Assuming structure: repo_root/libs/inference_djinn/scripts/build/python_builders/vllm/
        return script_dir.parent.parent.parent.parent.parent.parent

    def _find_latest_release_tag(self) -> str | None:
        """
        Find latest vLLM release tag from repository.

        Returns:
            Latest release tag (e.g., "v0.12.0") or None if no releases found.
        """
        result = subprocess.run(
            ["git", "tag", "--sort=-version:refname"],
            cwd=self.source_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.warning("   ⚠️  Failed to fetch tags")
            return None

        # Filter to release tags (vX.Y.Z format only)
        import re

        tags = [
            line.strip() for line in result.stdout.strip().split("\n") if line.strip()
        ]
        release_tags = [t for t in tags if re.match(r"^v\d+\.\d+\.\d+$", t)]

        if release_tags:
            return release_tags[0]  # Already sorted, first is latest

        return None

    def _detect_vllm_version(self) -> str:
        """
        Detect vLLM version from checked-out source.

        Uses git describe to get version from tags, falling back to
        the configured vllm_version if available.

        Returns:
            Version string (e.g., "0.13.2", "0.14.0", "main")
        """
        # If explicit version was requested, use it
        if self.vllm_version:
            logger.info(f"   📌 Using requested version: {self.vllm_version}")
            return self.vllm_version

        # Try git describe to get version from tags
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=self.source_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            tag = result.stdout.strip()
            # Strip 'v' prefix for consistency
            version = tag.lstrip("v")
            logger.info(f"   📌 Detected version from tag: {version}")
            return version

        # Fallback: check if on a release tag directly
        result = subprocess.run(
            ["git", "describe", "--tags", "--exact-match"],
            cwd=self.source_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            tag = result.stdout.strip()
            version = tag.lstrip("v")
            logger.info(f"   📌 Detected version (exact tag): {version}")
            return version

        # Fallback: assume main branch (bleeding edge)
        logger.warning("   ⚠️  Could not detect version, assuming 'main'")
        return "main"

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

    def build(self) -> str | None:
        """Execute complete build process with cleanup on failure.

        Returns:
            Path to built wheel if wheel_only mode, else None.
        """
        logger.info("=" * 80)
        logger.info("🚀 vLLM Wheel Builder")
        logger.info("=" * 80)
        logger.info("")

        # Display configuration
        logger.info(self.config.summary())
        if self.target_dir:
            logger.info(f"Target directory: {self.target_dir}")
        if self.wheel_only:
            logger.info("Mode: wheel-only (no installation)")
        logger.info("")

        wheel_path: str | None = None
        skip_cleanup = self.wheel_only  # Don't cleanup if we need the wheel

        try:
            # Execute build steps
            self._ensure_prerequisites()
            self._ensure_source_tree()
            self._apply_architecture_patches()  # Apply CMake patches for optimized builds
            self._configure_build_environment()
            self._build_wheel()

            if self.wheel_only:
                # Just return the wheel path, don't install
                wheel_path = str(self._wheel_file) if self._wheel_file else None
                logger.info("")
                logger.info("=" * 80)
                logger.info(f"✅ vLLM wheel built: {wheel_path}")
                logger.info("=" * 80)
            else:
                self._install_dependencies()  # Install deps from wheel METADATA
                self._install_wheel()
                self._verify_installation()

                logger.info("")
                logger.info("=" * 80)
                logger.info("✅ vLLM build complete!")
                logger.info("=" * 80)

                # Clean up source directory after successful install
                self._cleanup_source_dir()

        except KeyboardInterrupt:
            logger.error("\n❌ Build interrupted by user (Ctrl+C)")
            logger.info(f"   Source preserved for debugging: {self.extra_sources_dir}")
            raise

        except Exception as e:
            logger.error(f"\n❌ Build failed: {e}")
            logger.info(f"   Source preserved for debugging: {self.extra_sources_dir}")
            raise

        finally:
            # Clean up temp wheel directory (unless wheel_only mode)
            if not skip_cleanup:
                self._cleanup_wheel_dir()

        return wheel_path

    def _install_pytorch_nightly(self):
        """Install PyTorch nightly with CUDA 13.0 support."""
        logger.info("📦 Installing PyTorch nightly with CUDA 13.0 support...")

        # Find install script (relative to this builder)
        install_script = _script_dir.parent.parent / "install_pytorch_nightly.sh"

        if install_script.exists():
            logger.info(f"   Running: {install_script}")
            result = subprocess.run(
                ["bash", str(install_script)],
                capture_output=False,  # Show output
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"PyTorch nightly installation failed (exit code {result.returncode})"
                )
        else:
            # Fallback: direct pip install (for Docker builds where script may not exist)
            logger.info("   Install script not found, using pip install directly...")
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--no-cache-dir",
                    "torch",
                    "torchvision",
                    "torchaudio",
                    "--index-url",
                    "https://download.pytorch.org/whl/nightly/cu130",
                    "--pre",
                ],
                check=True,
            )

        # Force reimport of torch after installation
        if "torch" in sys.modules:
            del sys.modules["torch"]
        if "torchvision" in sys.modules:
            del sys.modules["torchvision"]
        if "torchaudio" in sys.modules:
            del sys.modules["torchaudio"]

        logger.info("   ✅ PyTorch nightly installed")

    def _ensure_prerequisites(self):
        """Check prerequisites, installing PyTorch nightly if needed.

        When target_dir is set (Docker builds), PyTorch should already be installed
        to the target directory. We skip installation and just verify it's present.
        """
        logger.info("🔍 Checking prerequisites...")

        # When using --target, PyTorch should be pre-installed by the Dockerfile
        # to the target directory. We don't install it ourselves because:
        # 1. pip --target doesn't handle binaries well (torch_shm_manager issue)
        # 2. The Dockerfile installs PyTorch before calling this builder
        skip_pytorch_install = self.target_dir is not None
        if skip_pytorch_install:
            logger.info("   ℹ️  Using --target mode: expecting PyTorch pre-installed")

        # Check PyTorch nightly - install if missing or outdated
        try:
            import torch
            from packaging import version

            pytorch_version = torch.__version__
            # Use torch.version.cuda (compiled CUDA version) instead of torch.cuda.is_available()
            # torch.cuda.is_available() returns False during Docker builds (no GPU attached)
            # but torch.version.cuda is always available if PyTorch was compiled with CUDA
            cuda_version = getattr(torch.version, "cuda", None)

            logger.info(f"   Found PyTorch {pytorch_version}")
            if cuda_version:
                logger.info(f"   Found CUDA {cuda_version} (compiled support)")

            # Verify nightly build (use version parsing)
            # Strip +cu130 suffix for version comparison
            pytorch_version_base = pytorch_version.split("+")[0]

            needs_install = False
            try:
                if version.parse(pytorch_version_base) < version.parse(
                    self.REQUIRED_PYTORCH_MIN_VERSION
                ):
                    logger.warning(f"   ⚠️  PyTorch version too old: {pytorch_version}")
                    needs_install = True
            except version.InvalidVersion:
                # Fallback to string prefix check if version parsing fails
                if not pytorch_version_base.startswith("2.10.0.dev"):
                    logger.warning(
                        f"   ⚠️  PyTorch version not recognized: {pytorch_version}"
                    )
                    needs_install = True

            # Verify CUDA 13.0 support
            if not cuda_version:
                logger.warning("   ⚠️  CUDA not available in PyTorch installation")
                needs_install = True
            elif not cuda_version.startswith(self.REQUIRED_CUDA_VERSION):
                logger.warning(
                    f"   ⚠️  Wrong CUDA version: {cuda_version} (need {self.REQUIRED_CUDA_VERSION})"
                )
                needs_install = True

            if needs_install:
                if skip_pytorch_install:
                    # In --target mode, don't try to install - fail with helpful message
                    raise RuntimeError(
                        f"❌ PyTorch in target directory does not meet requirements!\n"
                        f"   Found: {pytorch_version} (CUDA {cuda_version})\n"
                        f"   Required: >={self.REQUIRED_PYTORCH_MIN_VERSION} with CUDA {self.REQUIRED_CUDA_VERSION}\n"
                        f"   The Dockerfile should install PyTorch nightly to /build/packages\n"
                        f"   before calling this builder with --target."
                    )

                self._install_pytorch_nightly()
                # Re-import and verify
                import importlib

                torch = importlib.import_module("torch")
                pytorch_version = torch.__version__
                cuda_version = getattr(torch.version, "cuda", None)

                # Validate installed version meets requirements
                pytorch_version_base = pytorch_version.split("+")[0]
                try:
                    if version.parse(pytorch_version_base) < version.parse(
                        self.REQUIRED_PYTORCH_MIN_VERSION
                    ):
                        raise RuntimeError(
                            f"❌ PyTorch installation did not produce required version!\n"
                            f"   Installed: {pytorch_version}\n"
                            f"   Required: >={self.REQUIRED_PYTORCH_MIN_VERSION}\n"
                            f"   Try: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu130"
                        )
                except TypeError:
                    logger.warning(
                        f"   ⚠️  Could not parse version: {pytorch_version_base}"
                    )

                if not cuda_version or not cuda_version.startswith(
                    self.REQUIRED_CUDA_VERSION
                ):
                    raise RuntimeError(
                        f"❌ PyTorch installation missing CUDA {self.REQUIRED_CUDA_VERSION} support!\n"
                        f"   Found CUDA: {cuda_version}\n"
                        f"   Required: {self.REQUIRED_CUDA_VERSION}*\n"
                        f"   Try: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu130"
                    )

            logger.info(
                f"   ✅ PyTorch nightly ({pytorch_version}) with CUDA {cuda_version}"
            )

            # Verify PyTorch modules (vision, audio) are available
            # These are bundled with PyTorch and should not be overwritten
            try:
                import torchvision

                logger.info(f"   ✅ torchvision {torchvision.__version__}")
            except ImportError:
                logger.warning("   ⚠️  torchvision not found (optional)")

            try:
                import torchaudio

                logger.info(f"   ✅ torchaudio {torchaudio.__version__}")
            except ImportError:
                logger.warning("   ⚠️  torchaudio not found (optional)")

        except ImportError:
            # PyTorch not found
            if skip_pytorch_install:
                # In --target mode, PyTorch must be pre-installed
                raise RuntimeError(
                    "❌ PyTorch not found in target directory!\n"
                    "   When using --target, PyTorch must be pre-installed.\n"
                    "   The Dockerfile should install PyTorch nightly to /build/packages\n"
                    "   before calling this builder with --target.\n"
                    "   Ensure PYTHONPATH includes the target directory."
                )

            logger.warning("   ⚠️  PyTorch not found, installing...")
            self._install_pytorch_nightly()

            # Verify installation
            try:
                import torch
                from packaging import version

                pytorch_version = torch.__version__
                cuda_version = getattr(torch.version, "cuda", None)

                # Validate installed version meets requirements
                pytorch_version_base = pytorch_version.split("+")[0]
                try:
                    if version.parse(pytorch_version_base) < version.parse(
                        self.REQUIRED_PYTORCH_MIN_VERSION
                    ):
                        raise RuntimeError(
                            f"❌ PyTorch installation did not produce required version!\n"
                            f"   Installed: {pytorch_version}\n"
                            f"   Required: >={self.REQUIRED_PYTORCH_MIN_VERSION}\n"
                            f"   Try: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu130"
                        )
                except TypeError:
                    logger.warning(
                        f"   ⚠️  Could not parse version: {pytorch_version_base}"
                    )

                if not cuda_version or not cuda_version.startswith(
                    self.REQUIRED_CUDA_VERSION
                ):
                    raise RuntimeError(
                        f"❌ PyTorch installation missing CUDA {self.REQUIRED_CUDA_VERSION} support!\n"
                        f"   Found CUDA: {cuda_version}\n"
                        f"   Required: {self.REQUIRED_CUDA_VERSION}*\n"
                        f"   Try: pip install --pre torch --index-url https://download.pytorch.org/whl/nightly/cu130"
                    )

                logger.info(
                    f"   ✅ PyTorch nightly ({pytorch_version}) with CUDA {cuda_version}"
                )
            except ImportError:
                raise RuntimeError(
                    "❌ PyTorch installation failed!\n"
                    "   Please install manually:\n"
                    "   ./scripts/install_pytorch_nightly.sh"
                )

    def _ensure_source_tree(self):
        """Clone or update vLLM source with version selection logic.

        Version Strategy:
            None (default): Latest release tag
            "main": Latest main branch (bleeding edge)
            tag/hash: Specific version
        """
        logger.info("📥 Ensuring vLLM source tree...")

        self.extra_sources_dir.mkdir(parents=True, exist_ok=True)

        if (self.source_dir / ".git").exists():
            logger.info(f"   ✅ Source tree present: {self.source_dir}")
            # Fetch latest (including tags)
            logger.info("   🔄 Fetching latest vLLM and tags...")
            subprocess.run(
                ["git", "fetch", "--tags", "origin"],
                cwd=self.source_dir,
                check=False,
            )
        else:
            # Clone vLLM with retry logic
            logger.info(f"   📦 Cloning vLLM to {self.source_dir}...")
            repo_url = os.environ.get(
                "VLLM_REPO_URL", "https://github.com/vllm-project/vllm.git"
            )
            git_clone_with_retry(repo_url, self.source_dir)

            # Fetch tags after initial clone
            logger.info("   🔄 Fetching tags...")
            subprocess.run(
                ["git", "fetch", "--tags", "origin"],
                cwd=self.source_dir,
                check=False,
            )

        # Version selection logic
        if self.vllm_version == "main":
            # Explicit request for bleeding edge
            logger.info("   🔄 Using latest main branch (bleeding edge)...")
            result = subprocess.run(
                ["git", "checkout", "origin/main"],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to checkout main branch: {result.stderr.strip()}"
                )
        elif self.vllm_version is not None:
            # Explicit version pinning (tag or commit hash)
            logger.info(f"   🔄 Checking out version {self.vllm_version}...")
            result = subprocess.run(
                ["git", "checkout", self.vllm_version],
                cwd=self.source_dir,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                logger.error(f"   ❌ Failed to checkout {self.vllm_version}")
                logger.error(f"   {result.stderr.strip()}")
                raise RuntimeError(
                    f"Failed to checkout vLLM version: {self.vllm_version}\n"
                    f"Valid values: 'main', release tag (e.g., 'v0.12.0'), or commit hash"
                )
        else:
            # DEFAULT: Use latest release tag
            logger.info("   🔄 Finding latest release tag (default)...")
            latest_tag = self._find_latest_release_tag()

            if latest_tag:
                logger.info(f"   🔄 Using latest release: {latest_tag}")
                result = subprocess.run(
                    ["git", "checkout", latest_tag],
                    cwd=self.source_dir,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    logger.warning(
                        f"   ⚠️  Failed to checkout {latest_tag}, falling back to main"
                    )
                    subprocess.run(
                        ["git", "checkout", "origin/main"],
                        cwd=self.source_dir,
                        check=False,
                    )
            else:
                logger.warning(
                    "   ⚠️  No release tags found, falling back to main branch"
                )
                subprocess.run(
                    ["git", "checkout", "origin/main"],
                    cwd=self.source_dir,
                    check=False,
                )

        # Update submodules after checkout
        if (self.source_dir / ".gitmodules").exists():
            logger.info("   🔄 Updating git submodules...")
            subprocess.run(
                ["git", "submodule", "update", "--init", "--recursive"],
                cwd=self.source_dir,
                check=False,
            )

        # Log current commit for debugging
        commit = subprocess.run(
            ["git", "log", "-1", "--oneline"],
            cwd=self.source_dir,
            capture_output=True,
            text=True,
        )
        logger.info(f"   ✅ vLLM at: {commit.stdout.strip()}")

        # Detect version for patch workflow selection
        self._detected_version = self._detect_vllm_version()

    def _configure_build_environment(self):
        """Configure build environment variables."""
        logger.info("⚙️  Configuring build environment...")

        # CPU flags
        os.environ["CFLAGS"] = self.config.cpu_flags
        os.environ["CXXFLAGS"] = self.config.cpu_flags
        logger.info(f"   CPU flags: {self.config.cpu_flags}")

        # GPU architecture - set both PyTorch and CMake style
        os.environ["TORCH_CUDA_ARCH_LIST"] = self.config.torch_cuda_arch_list
        # Also set CMAKE_CUDA_ARCHITECTURES for CMake (semicolon-separated)
        cmake_archs = self.config.cuda_architectures  # e.g., "80;86;87;89;90;120"
        os.environ["CMAKE_CUDA_ARCHITECTURES"] = cmake_archs
        logger.info(f"   CUDA architectures: {self.config.torch_cuda_arch_list}")
        logger.info(f"   CMAKE_CUDA_ARCHITECTURES: {cmake_archs}")

        # Build parallelism
        os.environ["MAX_JOBS"] = str(self.config.max_jobs)
        logger.info(f"   Parallel jobs: {self.config.max_jobs}")

        # vLLM-specific flags
        os.environ["VLLM_TARGET_DEVICE"] = "cuda"
        os.environ["VLLM_INSTALL_PUNICA_KERNELS"] = "1"

        # Skip Marlin kernels for multi-arch builds (they require specific arch)
        # Marlin generation often fails on portable builds
        if self.config.gpu_mode == GPUMode.GENERIC:
            os.environ["VLLM_USE_MARLIN_KERNELS"] = "0"
            logger.info("   Marlin kernels: DISABLED (multi-arch build)")

        # For optimized builds, set Flash Attention architecture overrides
        if (
            self.config.gpu_mode == GPUMode.OPTIMIZED
            or self.config.gpu_mode == GPUMode.CUSTOM
        ):
            # Force Flash Attention to target specific architecture
            arch_dotted = self.config.torch_cuda_arch_list  # e.g., "12.0"
            os.environ["VLLM_FLASH_ATTN_FA2_ARCHS"] = arch_dotted
            # Disable FA3 for single-arch builds (compatibility)
            os.environ["VLLM_FLASH_ATTN_FA3_ARCHS"] = ""
            os.environ["VLLM_FLASH_ATTN_VERSION"] = "2"
            logger.info(
                f"   Flash Attention: FA2 targeting SM_{self.config.gpu_arch} (FA3 disabled)"
            )

        logger.info("   ✅ Environment configured")

    def _apply_architecture_patches(self):
        """
        Apply CMake patches for optimized builds using versioned patch workflows.

        Patches are version-gated: only applied by default for verified-working
        versions (v0.13.x). Other versions require --apply-patches flag.

        Only applied for OPTIMIZED or CUSTOM builds (single architecture).
        """
        # Skip patching for generic builds
        if self.config.gpu_mode == GPUMode.GENERIC:
            logger.info("⚙️  Skipping architecture patches (generic build)")
            return

        if self.config.gpu_arch is None:
            logger.warning("⚠️  GPU architecture not set, skipping patches")
            return

        # Get detected version (set during _ensure_source_tree)
        version = self._detected_version or "main"

        # Lazy import of VersionedPatcher to avoid circular import issues
        # The patch_workflows subpackage is loaded at runtime when needed
        # Use absolute import since python_builders is in sys.path
        from vllm.patch_workflows.patcher import VersionedPatcher

        # Use versioned patcher with workflow-based patches
        patcher = VersionedPatcher(
            source_dir=self.source_dir,
            gpu_arch=self.config.gpu_arch,
            vllm_version=version,
            force_patches=self.force_patches,
        )
        patcher.apply_patches()

    def _install_build_dependencies(self):
        """Install build dependencies required for --no-build-isolation."""
        logger.info("📦 Installing build dependencies...")

        # Build dependencies from vLLM's pyproject.toml (excluding torch)
        # We skip torch because we want to use the existing nightly PyTorch
        build_deps = [
            "cmake>=3.26.1",
            "ninja",
            "packaging>=24.2",
            "setuptools>=77.0.3,<81.0.0",
            "setuptools-scm>=8.0",
            "wheel",
            "jinja2",
        ]

        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                "--break-system-packages",
            ]
            + build_deps,
            check=True,
        )
        logger.info("   ✅ Build dependencies installed")

    def _clean_cmake_cache(self):
        """Clean CMake cache to avoid stale paths from previous builds.

        This is critical when using --no-build-isolation because CMake may cache
        paths to tools (like ninja) from previous isolated builds.
        """
        import shutil

        cache_dirs = ["build", ".deps", "CMakeFiles"]
        cache_files = ["CMakeCache.txt"]

        cleaned = []
        for dirname in cache_dirs:
            path = self.source_dir / dirname
            if path.exists():
                shutil.rmtree(path)
                cleaned.append(dirname)

        for filename in cache_files:
            path = self.source_dir / filename
            if path.exists():
                path.unlink()
                cleaned.append(filename)

        if cleaned:
            logger.info(f"   🧹 Cleaned CMake cache: {', '.join(cleaned)}")

    def _build_wheel(self):
        """Build the wheel to a temp directory."""
        logger.info("")
        logger.info("🔨 Building vLLM wheel...")
        logger.info(
            f"   This may take 20-40 minutes depending on parallelism ({self.config.max_jobs} jobs)"
        )
        logger.info("")

        # Clean CMake cache to avoid stale paths from previous builds
        self._clean_cmake_cache()

        # Install build dependencies first (required for --no-build-isolation)
        self._install_build_dependencies()

        # Create temp directory for wheel output
        self._wheel_dir = Path(tempfile.mkdtemp(prefix="vllm_wheel_"))
        logger.info(f"   Wheel output: {self._wheel_dir}")

        # Build wheel
        # CRITICAL: --no-build-isolation ensures we compile against
        # the existing nightly PyTorch instead of pip installing torch==2.9.0
        # in an isolated build environment (which would cause ABI mismatch)
        build_cmd = [
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "-v",  # Verbose output to see CMake errors
            "--wheel-dir",
            str(self._wheel_dir),
            str(self.source_dir),
        ]
        logger.info(f"   Running: {' '.join(build_cmd)}")

        # Log environment variables that affect the build
        logger.info("   Build environment:")
        for var in [
            "TORCH_CUDA_ARCH_LIST",
            "MAX_JOBS",
            "CFLAGS",
            "CXXFLAGS",
            "VLLM_TARGET_DEVICE",
            "CMAKE_CUDA_ARCHITECTURES",
        ]:
            val = os.environ.get(var, "(not set)")
            logger.info(f"     {var}={val}")

        result = subprocess.run(
            build_cmd,
            cwd=self.source_dir,
            capture_output=False,  # Show output in real-time
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Wheel build failed with exit code {result.returncode}. "
                f"Check the output above for CMake/compiler errors."
            )

        # Find the built wheel
        wheel_files = list(self._wheel_dir.glob("vllm-*.whl"))
        if wheel_files:
            self._wheel_file = max(wheel_files, key=lambda p: p.stat().st_mtime)
            logger.info(f"   ✅ Wheel built: {self._wheel_file.name}")
        else:
            raise RuntimeError(f"No vLLM wheel found in {self._wheel_dir}")

    def _find_deps_script(self) -> Path | None:
        """Find auto_install_dependencies.py in multiple possible locations.

        Works for both bare-metal (repo structure) and Docker builds.
        """
        script_dir = Path(__file__).parent.resolve()

        # Try multiple locations
        candidates = [
            # Bare-metal: libs/inference_djinn/scripts/build/install/vllm/auto_install_dependencies.py
            script_dir.parent.parent
            / "install"
            / "vllm"
            / "auto_install_dependencies.py",
            # Docker: /build/vllm_install_scripts/auto_install_dependencies.py
            Path("/build/vllm_install_scripts/auto_install_dependencies.py"),
            # Legacy Docker path (for older builds)
            Path("/build/vllm_scripts/auto_install_dependencies.py"),
            # Same directory (fallback)
            script_dir / "auto_install_dependencies.py",
        ]

        for candidate in candidates:
            if candidate.exists():
                logger.debug(f"   Found deps script: {candidate}")
                return candidate

        logger.warning(f"   Searched locations: {[str(c) for c in candidates]}")
        return None

    def _install_dependencies(self):
        """Install dependencies from wheel METADATA before installing vLLM."""
        if self._wheel_file is None:
            raise RuntimeError("Wheel file not set - build step may have failed")

        logger.info("")
        logger.info("📦 Installing vLLM dependencies from wheel METADATA...")

        # Find the dependency installer script
        deps_script = self._find_deps_script()

        if deps_script is None:
            logger.error("   ❌ CRITICAL: Dependency installer not found!")
            logger.error("   Searched locations:")
            logger.error("     - libs/inference_djinn/scripts/build/install/vllm/")
            logger.error("     - /build/vllm_install_scripts/")
            logger.error("   vLLM will NOT work without dependencies!")
            raise RuntimeError(
                "auto_install_dependencies.py not found - cannot install vLLM dependencies"
            )

        logger.info(f"   Using: {deps_script}")

        # Build command with optional --target
        cmd = [sys.executable, str(deps_script)]
        if self.target_dir:
            cmd.extend(["--target", str(self.target_dir)])
        # Always protect PyTorch for source builds (PyTorch nightly pre-installed)
        cmd.append("--protect-pytorch")
        cmd.append(str(self._wheel_file))

        result = subprocess.run(
            cmd,
            check=False,  # Don't fail build if deps have issues
        )

        if result.returncode != 0:
            logger.warning("   ⚠️  Some dependencies may not have installed correctly")
            logger.warning("   vLLM may still work - check for import errors")

    def _install_wheel(self):
        """Install the built wheel."""
        if self._wheel_file is None:
            raise RuntimeError("Wheel file not set - build step may have failed")

        logger.info("📦 Installing vLLM wheel...")
        logger.info(f"   Wheel: {self._wheel_file.name}")
        if self.target_dir:
            logger.info(f"   Target: {self.target_dir}")

        # Build pip install command
        cmd = [
            "pip",
            "install",
            "--no-deps",
            "--force-reinstall",
            "--break-system-packages",
        ]
        if self.target_dir:
            cmd.extend(["--target", str(self.target_dir)])
        cmd.append(str(self._wheel_file))

        # Install with --no-deps to preserve PyTorch nightly
        subprocess.run(cmd, check=True)

        logger.info("   ✅ vLLM installed")

    def _verify_installation(self):
        """Verify installation after build."""
        logger.info("🔍 Verifying installation...")

        # Verify PyTorch nightly is still installed (not overwritten)
        try:
            import torch

            pytorch_version = torch.__version__
            cuda_version = torch.version.cuda

            from packaging import version

            pytorch_version_base = pytorch_version.split("+")[0]

            try:
                if version.parse(pytorch_version_base) < version.parse(
                    self.REQUIRED_PYTORCH_MIN_VERSION
                ):
                    raise RuntimeError(
                        f"❌ PyTorch nightly was overwritten during build!\n"
                        f"   Found: {pytorch_version}\n"
                        f"   Expected: >={self.REQUIRED_PYTORCH_MIN_VERSION}\n"
                        f"   This should not happen - check build process."
                    )
            except version.InvalidVersion:
                if not pytorch_version_base.startswith("2.10.0.dev"):
                    raise RuntimeError(
                        f"❌ PyTorch nightly was overwritten during build!\n"
                        f"   Found: {pytorch_version}\n"
                        f"   Expected: >=2.10.0.dev*\n"
                        f"   This should not happen - check build process."
                    )

            if not cuda_version or not cuda_version.startswith(
                self.REQUIRED_CUDA_VERSION
            ):
                raise RuntimeError(
                    f"❌ CUDA support was lost during build!\n"
                    f"   Found: {cuda_version}\n"
                    f"   Expected: {self.REQUIRED_CUDA_VERSION}*\n"
                    f"   This should not happen - check build process."
                )

            logger.info(f"   ✅ PyTorch nightly preserved: {pytorch_version}")
            logger.info(f"   ✅ CUDA support preserved: {cuda_version}")

        except ImportError:
            raise RuntimeError(
                "❌ PyTorch was removed during build!\n"
                "   This should not happen - check build process."
            )

        # Verify vLLM was installed
        # We can't import vllm directly because dependencies aren't installed yet
        vllm_version = self._get_vllm_version()
        if vllm_version:
            logger.info(f"   ✅ vLLM installed: {vllm_version}")
        else:
            raise RuntimeError(
                "❌ vLLM not found after installation!\n"
                "   Check wheel installation logs for errors."
            )

    def _get_vllm_version(self) -> str | None:
        """
        Get vLLM version without importing the module.

        When target_dir is set, checks the target directory directly.
        Otherwise, uses pip show (for system installs).
        """
        if self.target_dir:
            return self._get_vllm_version_from_target()
        else:
            return self._get_vllm_version_from_pip()

    def _get_vllm_version_from_target(self) -> str | None:
        """
        Get vLLM version from target directory.

        Packages installed with --target are not in pip's database,
        so we check for the package files directly.
        """
        if not self.target_dir or not self.target_dir.exists():
            return None

        # Check for vllm package directory
        vllm_pkg = self.target_dir / "vllm"
        if not vllm_pkg.exists():
            logger.warning(f"   ⚠️  vllm package not found in {self.target_dir}")
            return None

        # Try to get version from dist-info METADATA
        for item in self.target_dir.iterdir():
            if item.name.startswith("vllm-") and item.name.endswith(".dist-info"):
                metadata_file = item / "METADATA"
                if metadata_file.exists():
                    try:
                        content = metadata_file.read_text()
                        for line in content.split("\n"):
                            if line.startswith("Version:"):
                                return line.split(":", 1)[1].strip()
                    except Exception as e:
                        logger.warning(f"   ⚠️  Failed to read METADATA: {e}")

        # Fallback: check for __version__ in __init__.py (less reliable)
        init_file = vllm_pkg / "__init__.py"
        if init_file.exists():
            try:
                content = init_file.read_text()
                import re

                match = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', content)
                if match:
                    return match.group(1)
            except Exception:
                pass

        # Package exists but version unknown
        return "unknown"

    def _get_vllm_version_from_pip(self) -> str | None:
        """
        Get vLLM version from pip (for system installs).

        This is necessary because vLLM can't be imported until dependencies
        are installed, but we want to verify the wheel installed correctly.
        """
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "show", "vllm"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                for line in result.stdout.split("\n"):
                    if line.startswith("Version:"):
                        return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return None
